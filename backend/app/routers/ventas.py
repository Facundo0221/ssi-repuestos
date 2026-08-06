"""
routers/ventas.py — Ventas, cierre de caja, estadísticas y reportes financieros
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, text as sql_text
from typing import Optional, List
from datetime import date, datetime
from decimal import Decimal

from app.database import get_db
from app import models, schemas
from app.auth import get_current_user, require_gerente

router = APIRouter(prefix="/ventas", tags=["Ventas"])


def _verificar_acceso_local(usuario: models.Usuario, local_id: int):
    """Vendedor solo puede operar en su local asignado. Gerente/root en cualquiera."""
    if usuario.rol in ("root", "gerente"):
        return
    if usuario.local_id != local_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a ese local")


def _descontar_stock_local(db: Session, producto_id: int, local_id: int, cantidad: int):
    """
    Descuenta stock del local específico y actualiza el stock global (suma).
    Lanza 400 si no hay suficiente stock en ese local.
    """
    sl = db.query(models.StockLocal).filter(
        models.StockLocal.producto_id == producto_id,
        models.StockLocal.local_id    == local_id,
    ).with_for_update().first()

    if not sl or sl.cantidad < cantidad:
        disponible = sl.cantidad if sl else 0
        prod = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
        nombre = prod.nombre if prod else f"ID {producto_id}"
        raise HTTPException(
            status_code=400,
            detail=f"Stock insuficiente en este local para '{nombre}' "
                   f"(disponible: {disponible}, pedido: {cantidad})",
        )

    sl.cantidad -= cantidad

    # Sincronizar stock global
    db.execute(
        sql_text("""
            UPDATE productos
            SET stock = (
                SELECT COALESCE(SUM(cantidad), 0)
                FROM stock_local
                WHERE producto_id = :pid
            )
            WHERE id = :pid
        """),
        {"pid": producto_id},
    )


def _devolver_stock_local(db: Session, producto_id: int, local_id: int, cantidad: int):
    """Devuelve stock al anular una venta."""
    sl = db.query(models.StockLocal).filter(
        models.StockLocal.producto_id == producto_id,
        models.StockLocal.local_id    == local_id,
    ).first()

    if sl:
        sl.cantidad += cantidad
    else:
        db.add(models.StockLocal(
            producto_id=producto_id,
            local_id=local_id,
            cantidad=cantidad,
        ))

    db.execute(
        sql_text("""
            UPDATE productos
            SET stock = (
                SELECT COALESCE(SUM(cantidad), 0)
                FROM stock_local WHERE producto_id = :pid
            )
            WHERE id = :pid
        """),
        {"pid": producto_id},
    )


# ── CREAR VENTA ───────────────────────────────────────────────────────────────
@router.post("/local/{local_id}", response_model=schemas.VentaOut, status_code=201)
def crear_venta(
    local_id: int,
    data: schemas.VentaCreate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user),
):
    _verificar_acceso_local(current_user, local_id)

    local = db.query(models.Local).filter(
        models.Local.id == local_id, models.Local.activo == True
    ).first()
    if not local:
        raise HTTPException(status_code=404, detail="Local no encontrado")
    if not data.items:
        raise HTTPException(status_code=400, detail="La venta necesita al menos un ítem")

    # Validar productos y precios antes de tocar stock
    items_data = []
    for item in data.items:
        if item.cantidad <= 0:
            raise HTTPException(status_code=400, detail="La cantidad debe ser mayor a 0")
        prod = db.query(models.Producto).filter(
            models.Producto.id == item.producto_id,
            models.Producto.activo == True,
        ).first()
        if not prod:
            raise HTTPException(status_code=404, detail=f"Producto ID {item.producto_id} no encontrado")
        precio = prod.precio_oferta if prod.precio_oferta else prod.precio
        items_data.append((prod, item.cantidad, precio))

    total = sum(Decimal(str(c)) * p for _, c, p in items_data)

    venta = models.Venta(
        local_id=local_id,
        usuario_id=current_user.id,
        total=total,
        notas=data.notas,
    )
    db.add(venta)
    db.flush()  # obtener venta.id

    for prod, cantidad, precio in items_data:
        db.add(models.VentaItem(
            venta_id=venta.id,
            producto_id=prod.id,
            cantidad=cantidad,
            precio_unitario=precio,
            subtotal=Decimal(str(cantidad)) * precio,
        ))
        _descontar_stock_local(db, prod.id, local_id, cantidad)

    db.commit()
    db.refresh(venta)
    return venta


# ── ANULAR VENTA ──────────────────────────────────────────────────────────────
@router.post("/local/{local_id}/{venta_id}/anular", response_model=schemas.VentaOut)
def anular_venta(
    local_id: int,
    venta_id: int,
    data: schemas.VentaAnular,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_gerente),
):
    venta = db.query(models.Venta).filter(
        models.Venta.id == venta_id,
        models.Venta.local_id == local_id,
    ).first()
    if not venta:
        raise HTTPException(status_code=404, detail="Venta no encontrada")
    if venta.anulada:
        raise HTTPException(status_code=400, detail="La venta ya fue anulada")

    for item in venta.items:
        _devolver_stock_local(db, item.producto_id, local_id, item.cantidad)

    venta.anulada        = True
    venta.anulada_motivo = data.motivo
    venta.anulada_en     = datetime.utcnow()
    db.commit()
    db.refresh(venta)
    return venta


# ── LISTAR VENTAS ─────────────────────────────────────────────────────────────
@router.get("/local/{local_id}", response_model=list[schemas.VentaOut])
def listar_ventas_local(
    local_id: int,
    fecha_desde: Optional[date]  = Query(None),
    fecha_hasta: Optional[date]  = Query(None),
    incluir_anuladas: bool       = Query(False),
    skip: int                    = Query(0, ge=0),
    limit: int                   = Query(50, ge=1, le=200),
    db: Session                  = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user),
):
    _verificar_acceso_local(current_user, local_id)
    q = db.query(models.Venta).filter(models.Venta.local_id == local_id)
    if not incluir_anuladas:
        q = q.filter(models.Venta.anulada == False)
    if fecha_desde:
        q = q.filter(func.date(models.Venta.creado_en) >= fecha_desde)
    if fecha_hasta:
        q = q.filter(func.date(models.Venta.creado_en) <= fecha_hasta)
    return q.order_by(models.Venta.creado_en.desc()).offset(skip).limit(limit).all()


# ── DETALLE DE UNA VENTA ──────────────────────────────────────────────────────
@router.get("/local/{local_id}/{venta_id}", response_model=schemas.VentaOut)
def obtener_venta(
    local_id: int,
    venta_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user),
):
    _verificar_acceso_local(current_user, local_id)
    venta = db.query(models.Venta).filter(
        models.Venta.id == venta_id,
        models.Venta.local_id == local_id,
    ).first()
    if not venta:
        raise HTTPException(status_code=404, detail="Venta no encontrada")
    return venta


# ── CIERRE DE CAJA ────────────────────────────────────────────────────────────
@router.get("/cierre-caja", response_model=schemas.CierreCaja)
def cierre_caja(
    local_id: int   = Query(...),
    fecha: date     = Query(default_factory=date.today),
    db: Session     = Depends(get_db),
    _: models.Usuario = Depends(require_gerente),
):
    local = db.query(models.Local).filter(models.Local.id == local_id).first()
    if not local:
        raise HTTPException(status_code=404, detail="Local no encontrado")

    base         = db.query(models.Venta).filter(
        models.Venta.local_id == local_id,
        func.date(func.timezone('America/Argentina/Buenos_Aires', models.Venta.creado_en)) == fecha,
    )
    ventas_ok    = base.filter(models.Venta.anulada == False).all()
    cant_anuladas = base.filter(models.Venta.anulada == True).count()
    total_monto  = sum(v.total for v in ventas_ok)

    vendedores: dict = {}
    for v in ventas_ok:
        uid = v.usuario_id
        if uid not in vendedores:
            u = db.query(models.Usuario).filter(models.Usuario.id == uid).first()
            vendedores[uid] = {
                "usuario_id": uid,
                "username":   u.username    if u else None,
                "nombre_real": u.nombre_real if u else None,
                "cantidad_ventas": 0,
                "monto_total": Decimal("0"),
            }
        vendedores[uid]["cantidad_ventas"] += 1
        vendedores[uid]["monto_total"]     += v.total

    return schemas.CierreCaja(
        fecha=fecha, local_id=local_id, local_nombre=local.nombre,
        total_ventas=len(ventas_ok), monto_total=Decimal(str(total_monto)),
        ventas_anuladas=cant_anuladas,
        por_vendedor=[schemas.CierreVendedor(**v) for v in vendedores.values()],
    )


# ── ESTADÍSTICAS GENERALES ────────────────────────────────────────────────────
@router.get("/estadisticas/generales", response_model=schemas.StatsGenerales)
def estadisticas_generales(
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(require_gerente),
):
    hoy = date.today()

    total_prods = db.query(func.count(models.Producto.id)).filter(
        models.Producto.activo == True).scalar()
    sin_stock = db.query(func.count(models.Producto.id)).filter(
        models.Producto.activo == True, models.Producto.stock == 0).scalar()
    stock_bajo = db.query(func.count(models.Producto.id)).filter(
        models.Producto.activo == True,
        models.Producto.stock > 0,
        models.Producto.stock <= models.Producto.stock_minimo,
        models.Producto.stock_minimo > 0,
    ).scalar()

    ventas_hoy = db.query(
        func.count(models.Venta.id),
        func.coalesce(func.sum(models.Venta.total), 0),
    ).filter(
        func.date(func.timezone('America/Argentina/Buenos_Aires', models.Venta.creado_en)) == hoy,
        models.Venta.anulada == False,
    ).one()

    locales = db.query(models.Local).filter(models.Local.activo == True).all()
    por_local = []
    for loc in locales:
        row = db.query(
            func.count(models.Venta.id),
            func.coalesce(func.sum(models.Venta.total), 0),
            func.coalesce(func.sum(models.VentaItem.cantidad), 0),
        ).outerjoin(
            models.VentaItem, models.VentaItem.venta_id == models.Venta.id
        ).filter(
            models.Venta.local_id == loc.id,
            models.Venta.anulada  == False,
        ).one()
        por_local.append(schemas.StatsLocal(
            local_id=loc.id, local_nombre=loc.nombre,
            total_ventas=row[0],
            monto_total=Decimal(str(row[1])),
            productos_vendidos=row[2],
        ))

    return schemas.StatsGenerales(
        total_productos=total_prods,
        productos_sin_stock=sin_stock,
        productos_stock_bajo=stock_bajo,
        total_ventas_hoy=ventas_hoy[0],
        monto_ventas_hoy=Decimal(str(ventas_hoy[1])),
        por_local=por_local,
    )


# ── REPORTE FINANCIERO (lógica movida desde frontend) ────────────────────────
@router.get("/reportes/financiero", response_model=schemas.ReporteFinanciero)
def reporte_financiero(
    fecha_desde: date = Query(...),
    fecha_hasta: date = Query(...),
    local_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(require_gerente),
):
    """
    Calcula ganancia bruta = precio_venta - precio_costo por ítem.
    Toda la lógica en el backend — el frontend solo recibe los números.
    """
    q_base = db.query(models.VentaItem).join(models.Venta).filter(
        models.Venta.anulada == False,
        func.date(models.Venta.creado_en) >= fecha_desde,
        func.date(models.Venta.creado_en) <= fecha_hasta,
    )
    if local_id:
        q_base = q_base.filter(models.Venta.local_id == local_id)

    items = q_base.all()

    monto_ventas  = Decimal("0")
    costo_total   = Decimal("0")
    cant_ventas_ids = set()

    for item in items:
        subtotal = item.precio_unitario * item.cantidad
        costo_unit = item.producto.precio_costo or item.precio_unitario
        monto_ventas  += subtotal
        costo_total   += costo_unit * item.cantidad
        cant_ventas_ids.add(item.venta_id)

    ganancia_bruta = monto_ventas - costo_total
    margen_promedio = (
        (ganancia_bruta / costo_total * 100).quantize(Decimal("0.01"))
        if costo_total > 0 else Decimal("0")
    )

    # Ventas por mes
    meses: dict = {}
    for item in items:
        mes = item.venta.creado_en.strftime("%Y-%m")
        if mes not in meses:
            meses[mes] = {"total_ventas": 0, "ids": set(),
                          "monto_total": Decimal("0"), "ganancia_bruta": Decimal("0")}
        meses[mes]["ids"].add(item.venta_id)
        sub   = item.precio_unitario * item.cantidad
        costo = (item.producto.precio_costo or item.precio_unitario) * item.cantidad
        meses[mes]["monto_total"]    += sub
        meses[mes]["ganancia_bruta"] += sub - costo

    por_mes = [
        schemas.VentaMensual(
            mes=mes,
            total_ventas=len(v["ids"]),
            monto_total=v["monto_total"],
            ganancia_bruta=v["ganancia_bruta"],
        )
        for mes, v in sorted(meses.items())
    ]

    # Top 10 productos
    prods: dict = {}
    for item in items:
        pid = item.producto_id
        if pid not in prods:
            prods[pid] = {
                "producto_id": pid,
                "nombre": item.producto.nombre,
                "cantidad_vendida": 0,
                "monto_total": Decimal("0"),
                "ganancia_bruta": Decimal("0"),
            }
        sub   = item.precio_unitario * item.cantidad
        costo = (item.producto.precio_costo or item.precio_unitario) * item.cantidad
        prods[pid]["cantidad_vendida"] += item.cantidad
        prods[pid]["monto_total"]      += sub
        prods[pid]["ganancia_bruta"]   += sub - costo

    top_productos = sorted(
        [schemas.ProductoTopVentas(**p) for p in prods.values()],
        key=lambda x: x.monto_total, reverse=True
    )[:10]

    return schemas.ReporteFinanciero(
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        monto_ventas=monto_ventas,
        costo_total=costo_total,
        ganancia_bruta=ganancia_bruta,
        margen_promedio_pct=margen_promedio,
        cantidad_ventas=len(cant_ventas_ids),
        por_mes=por_mes,
        top_productos=top_productos,
    )


# ── VENTAS POR MES (para gráfica) ─────────────────────────────────────────────
@router.get("/reportes/ventas-por-mes")
def ventas_por_mes(
    meses: int = Query(12, ge=1, le=24),
    local_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(require_gerente),
):
    """Últimos N meses de ventas — optimizado para gráficas del dashboard."""
    where = "v.anulada = false"
    params: dict = {}
    if local_id:
        where += " AND v.local_id = :lid"
        params["lid"] = local_id

    rows = db.execute(sql_text(f"""
        SELECT
            TO_CHAR(DATE_TRUNC('month', v.creado_en), 'YYYY-MM') AS mes,
            COUNT(DISTINCT v.id)                                  AS cantidad,
            COALESCE(SUM(v.total), 0)                             AS monto
        FROM ventas v
        WHERE {where}
          AND v.creado_en >= NOW() - INTERVAL '{meses} months'
        GROUP BY 1
        ORDER BY 1
    """), params).fetchall()

    return [{"mes": r[0], "cantidad": r[1], "monto": float(r[2])} for r in rows]