"""
routers/productos.py — CRUD de productos + margen + stock
"""
import os, uuid, shutil
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import text as sql_text, or_
from pydantic import BaseModel
from typing import Optional
from decimal import Decimal

from app.database import get_db
from app import models, schemas
from app.auth import require_gerente, get_current_user

router    = APIRouter(prefix="/productos", tags=["Productos"])
UPLOAD_DIR = "/app/static/imagenes"
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ── RUTAS FIJAS primero (antes de /{producto_id}) ─────────────────────────────

@router.get("/barcode/{codigo}", response_model=schemas.ProductoMinimo)
def buscar_por_barcode(
    codigo: str,
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(get_current_user),   # requiere auth
):
    """Busca por código de barras. Requiere autenticación."""
    prod = db.query(models.Producto).filter(
        models.Producto.codigo_barras == codigo,
        models.Producto.activo == True,
    ).first()
    if not prod:
        raise HTTPException(status_code=404, detail=f"No existe producto con código '{codigo}'")
    return prod


# ── LISTAR ────────────────────────────────────────────────────────────────────
@router.get("/", response_model=list[schemas.ProductoOut])
def listar_productos(
    q: Optional[str]               = Query(None),
    subcategoria_id: Optional[int] = Query(None),
    categoria_id: Optional[int]    = Query(None),
    moto_110: Optional[bool]       = Query(None),
    moto_150: Optional[bool]       = Query(None),
    moto_200: Optional[bool]       = Query(None),
    marca: Optional[str]           = Query(None),
    solo_oferta: bool              = Query(False),
    solo_stock: bool               = Query(False),
    stock_bajo: bool               = Query(False),
    local_id: Optional[int]        = Query(None),   # filtrar por stock del local
    skip: int                      = Query(0, ge=0),
    limit: int                     = Query(50, ge=1, le=1000),
    db: Session                    = Depends(get_db),
    _: models.Usuario              = Depends(get_current_user),
):
    query = db.query(models.Producto).filter(models.Producto.activo == True)
    if q:
        query = query.filter(or_(
            models.Producto.nombre.ilike(f"%{q}%"),
            models.Producto.descripcion.ilike(f"%{q}%"),
            models.Producto.marca.ilike(f"%{q}%"),
            models.Producto.codigo_barras.ilike(f"%{q}%"),
        ))
    if subcategoria_id:
        query = query.filter(models.Producto.subcategoria_id == subcategoria_id)
    if categoria_id:
        subs = db.query(models.Subcategoria.id).filter(
            models.Subcategoria.categoria_id == categoria_id
        ).subquery()
        query = query.filter(models.Producto.subcategoria_id.in_(subs))
    if moto_110:  query = query.filter(models.Producto.moto_110 == True)
    if moto_150:  query = query.filter(models.Producto.moto_150 == True)
    if moto_200:  query = query.filter(models.Producto.moto_200 == True)
    if marca:     query = query.filter(models.Producto.marca.ilike(f"%{marca}%"))
    if solo_oferta: query = query.filter(models.Producto.precio_oferta != None)

    # Si se filtra por local: obtener stock_local y filtrar/inyectar
    if local_id:
        sl_rows = db.execute(
            sql_text("SELECT producto_id, cantidad FROM stock_local WHERE local_id = :lid"),
            {"lid": local_id}
        ).fetchall()
        stock_map = {r[0]: r[1] for r in sl_rows}
        if solo_stock:
            ids_con_stock = [pid for pid, qty in stock_map.items() if qty > 0]
            query = query.filter(models.Producto.id.in_(ids_con_stock))
        prods = query.offset(skip).limit(limit).all()
        for p in prods:
            p._stock_local = stock_map.get(p.id, 0)
        if stock_bajo:
            prods = [p for p in prods
                     if 0 < p._stock_local <= p.stock_minimo and p.stock_minimo > 0]
        return prods

    if solo_stock:  query = query.filter(models.Producto.stock > 0)
    if stock_bajo:  query = query.filter(
        models.Producto.stock <= models.Producto.stock_minimo,
        models.Producto.stock_minimo > 0,
    )
    return query.offset(skip).limit(limit).all()


# ── CATÁLOGO PÚBLICO (sin auth, sin precio_costo) ─────────────────────────────
@router.get("/publico", response_model=list[schemas.ProductoPublico])
def catalogo_publico(
    q: Optional[str]  = Query(None),
    solo_oferta: bool = Query(False),
    solo_stock: bool  = Query(True),
    skip: int         = Query(0, ge=0),
    limit: int        = Query(50, ge=1, le=100),
    db: Session       = Depends(get_db),
):
    """Endpoint público para el catálogo — NO expone precio_costo ni margen."""
    query = db.query(models.Producto).filter(models.Producto.activo == True)
    if q:
        query = query.filter(models.Producto.nombre.ilike(f"%{q}%"))
    if solo_oferta: query = query.filter(models.Producto.precio_oferta != None)
    if solo_stock:  query = query.filter(models.Producto.stock > 0)
    return query.offset(skip).limit(limit).all()


# ── OBTENER UNO ───────────────────────────────────────────────────────────────
@router.get("/{producto_id}", response_model=schemas.ProductoOut)
def obtener_producto(
    producto_id: int,
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(get_current_user),
):
    prod = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not prod:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return prod


# ── CREAR ─────────────────────────────────────────────────────────────────────
@router.post("/", response_model=schemas.ProductoOut, status_code=201)
def crear_producto(
    data: schemas.ProductoCreate,
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(require_gerente),
):
    if data.codigo_barras:
        existe = db.query(models.Producto).filter(
            models.Producto.codigo_barras == data.codigo_barras
        ).first()
        if existe:
            raise HTTPException(status_code=400, detail="El código de barras ya está en uso")
    nuevo = models.Producto(**data.model_dump())
    # Si no tiene precio_costo, usar precio como base
    if not nuevo.precio_costo:
        nuevo.precio_costo = nuevo.precio
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return nuevo


# ── ACTUALIZAR ────────────────────────────────────────────────────────────────
@router.patch("/{producto_id}", response_model=schemas.ProductoOut)
def actualizar_producto(
    producto_id: int,
    data: schemas.ProductoUpdate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_gerente),
):
    prod = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not prod:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    cambios = data.model_dump(exclude_unset=True)

    # Registrar historial si cambia el precio
    if "precio" in cambios and float(cambios["precio"]) != float(prod.precio):
        db.add(models.PrecioHistorial(
            producto_id=producto_id,
            precio_anterior=prod.precio,
            precio_nuevo=cambios["precio"],
            usuario_id=current_user.id,
        ))

    for campo, valor in cambios.items():
        setattr(prod, campo, valor)

    db.commit()
    db.refresh(prod)
    return prod


# ── ELIMINAR (soft delete) ────────────────────────────────────────────────────
@router.delete("/{producto_id}", status_code=204)
def eliminar_producto(
    producto_id: int,
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(require_gerente),
):
    prod = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not prod:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    prod.activo = False
    db.commit()


# ── HISTORIAL DE PRECIOS ──────────────────────────────────────────────────────
@router.get("/{producto_id}/historial-precios", response_model=list[schemas.PrecioHistorialOut])
def historial_precios(
    producto_id: int,
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(require_gerente),
):
    return db.query(models.PrecioHistorial).filter(
        models.PrecioHistorial.producto_id == producto_id
    ).order_by(models.PrecioHistorial.creado_en.desc()).all()


# ── AJUSTE MANUAL DE STOCK ────────────────────────────────────────────────────
@router.post("/{producto_id}/ajuste-stock", response_model=schemas.AjusteStockOut)
def ajustar_stock(
    producto_id: int,
    data: schemas.AjusteStockCreate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_gerente),
):
    """
    Ajuste auditado. cantidad positiva=entrada, negativa=salida.
    Si se indica local_id, actualiza stock_local además del global.
    """
    prod = db.query(models.Producto).filter(
        models.Producto.id == producto_id,
        models.Producto.activo == True,
    ).first()
    if not prod:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    # 1. Actualizar stock_local del local indicado (o el primero activo si no se indica)
    local_id_efectivo = data.local_id
    if not local_id_efectivo:
        # Sin local explícito: buscar el primer local activo
        primer_local = db.execute(
            sql_text("SELECT id FROM locales WHERE activo = TRUE ORDER BY id LIMIT 1")
        ).fetchone()
        if primer_local:
            local_id_efectivo = primer_local[0]

    if local_id_efectivo:
        sl = db.query(models.StockLocal).filter(
            models.StockLocal.producto_id == producto_id,
            models.StockLocal.local_id    == local_id_efectivo,
        ).with_for_update().first()

        cantidad_local_nueva = (sl.cantidad if sl else 0) + data.cantidad
        if cantidad_local_nueva < 0:
            raise HTTPException(
                status_code=400,
                detail=f"Stock resultante en ese local ({cantidad_local_nueva}) no puede ser negativo",
            )

        if sl:
            sl.cantidad = cantidad_local_nueva
        else:
            db.add(models.StockLocal(
                producto_id=producto_id,
                local_id=local_id_efectivo,
                cantidad=cantidad_local_nueva,
            ))
        db.flush()  # para que el UPDATE de suma lo incluya

    # 2. Recalcular stock global como suma de todos los locales
    stock_antes = prod.stock
    resultado = db.execute(
        sql_text("""
            UPDATE productos
            SET stock = (
                SELECT COALESCE(SUM(cantidad), 0)
                FROM stock_local
                WHERE producto_id = :pid
            )
            WHERE id = :pid
            RETURNING stock
        """),
        {"pid": producto_id},
    ).fetchone()
    nuevo_stock = resultado[0] if resultado else prod.stock
    prod.stock = nuevo_stock  # mantener el objeto en sync

    ajuste = models.AjusteStock(
        producto_id=producto_id,
        local_id=local_id_efectivo,
        cantidad=data.cantidad,
        stock_antes=stock_antes,
        stock_despues=nuevo_stock,
        motivo=data.motivo,
        usuario_id=current_user.id,
    )
    db.add(ajuste)
    db.commit()
    db.refresh(ajuste)
    return ajuste


# ── STOCK POR LOCAL ───────────────────────────────────────────────────────────
@router.get("/{producto_id}/stock-locales", response_model=list[schemas.StockLocalOut])
def stock_por_locales(
    producto_id: int,
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(get_current_user),
):
    """
    Devuelve el stock de este producto en TODOS los locales activos.
    Si no existe registro en stock_local para un local, devuelve cantidad=0.
    """
    rows = db.execute(sql_text("""
        SELECT
            l.id                     AS local_id,
            COALESCE(sl.cantidad, 0) AS cantidad
        FROM locales l
        LEFT JOIN stock_local sl
               ON sl.local_id     = l.id
              AND sl.producto_id  = :pid
        WHERE l.activo = TRUE
        ORDER BY l.id
    """), {"pid": producto_id}).fetchall()

    return [{"local_id": r[0], "cantidad": r[1]} for r in rows]


# ── IMAGEN LOCAL ──────────────────────────────────────────────────────────────
@router.post("/{producto_id}/imagen-local", response_model=schemas.ProductoOut)
async def subir_imagen_local(
    producto_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(require_gerente),
):
    prod = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not prod:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(status_code=400, detail="Formato no soportado. Usá jpg, png o webp")

    filename = f"{producto_id}_{uuid.uuid4().hex[:8]}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)
    with open(filepath, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        from PIL import Image
        with Image.open(filepath) as img:
            if img.width > 800 or img.height > 800:
                img.thumbnail((800, 800))
                img.save(filepath, optimize=True, quality=85)
    except Exception:
        pass

    # Borrar imagen local anterior
    if prod.imagen_url and prod.imagen_url.startswith("/static/"):
        old = os.path.join(UPLOAD_DIR, os.path.basename(prod.imagen_url))
        if os.path.exists(old):
            os.remove(old)

    prod.imagen_url = f"/static/imagenes/{filename}"
    db.commit()
    db.refresh(prod)
    return prod


# Alias para compatibilidad con código anterior
@router.post("/{producto_id}/imagen", response_model=schemas.ProductoOut)
async def subir_imagen_compat(
    producto_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    u: models.Usuario = Depends(require_gerente),
):
    return await subir_imagen_local(producto_id, file, db, u)


# ── IMAGEN POR URL ────────────────────────────────────────────────────────────
class ImagenUrlBody(BaseModel):
    imagen_url: Optional[str] = None

@router.patch("/{producto_id}/imagen-url", response_model=schemas.ProductoOut)
def actualizar_imagen_url(
    producto_id: int,
    body: ImagenUrlBody,
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(require_gerente),
):
    prod = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not prod:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    # Borrar imagen local si existía
    if prod.imagen_url and prod.imagen_url.startswith("/static/"):
        old = os.path.join(UPLOAD_DIR, os.path.basename(prod.imagen_url))
        if os.path.exists(old):
            os.remove(old)

    prod.imagen_url = body.imagen_url or None
    db.commit()
    db.refresh(prod)
    return prod


# ── APLICAR MARGEN (scope global) ────────────────────────────────────────────
class MargenRequest(BaseModel):
    margen_pct: float
    scope: str = "todos"        # "todos" | "sin_oferta" | "categoria"
    categoria_id: Optional[int] = None

class MargenResult(BaseModel):
    actualizados: int
    margen_pct: float

@router.post("/aplicar-margen", response_model=MargenResult)
def aplicar_margen(
    body: MargenRequest,
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(require_gerente),
):
    """
    Establece precio_venta = precio_costo × (1 + margen%/100) para el scope.
    Guarda margen_individual en cada producto — igual que el margen individual
    pero aplicado masivamente.
    Idempotente: aplicar 40% dos veces da el mismo resultado que una.
    """
    if body.margen_pct <= 0:
        raise HTTPException(status_code=400, detail="El margen debe ser mayor a 0")

    factor = 1 + body.margen_pct / 100
    where  = "activo = true"
    params: dict = {"factor": factor, "margen_pct": body.margen_pct}

    if body.scope == "sin_oferta":
        where += " AND (precio_oferta IS NULL OR precio_oferta = 0)"
    elif body.scope == "categoria" and body.categoria_id:
        where += " AND subcategoria_id = :cat_id"
        params["cat_id"] = body.categoria_id

    # Paso 1: poblar precio_costo donde falte
    db.execute(sql_text(f"""
        UPDATE productos SET precio_costo = precio
        WHERE precio_costo IS NULL AND {where}
    """), params)

    # Paso 2: historial
    db.execute(sql_text(f"""
        INSERT INTO precio_historial (producto_id, precio_anterior, precio_nuevo, usuario_id)
        SELECT id, precio, ROUND(precio_costo * :factor, 2), 1
        FROM productos WHERE {where}
    """), params)

    # Paso 3: actualizar precio + guardar margen_individual
    result = db.execute(sql_text(f"""
        UPDATE productos
        SET precio            = ROUND(precio_costo * :factor, 2),
            margen_individual = :margen_pct
        WHERE {where}
    """), params)

    db.commit()
    return MargenResult(actualizados=result.rowcount, margen_pct=body.margen_pct)