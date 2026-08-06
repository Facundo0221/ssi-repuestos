"""
importar.py v4 — Importar productos desde PDF de factura Lopez Hnos.

pdfplumber extrae cada fila en DOS líneas:
  Línea A: [CODE] NOMBRE_P1  CANTIDAD  PRECIO  DESC%  IVA  $ IMPORTE
  Línea B: NOMBRE_P2  Unidades  21%

Precio guardado = precio_unitario × (1 − descuento/100)
"""

import re, io, logging
from decimal import Decimal, InvalidOperation
from typing import Optional, List
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text as sql_text

from app.database import get_db
from app import models
from app.auth import require_gerente

log = logging.getLogger("importar")
router = APIRouter(prefix="/importar", tags=["Importar"])


def to_dec(s: str) -> Decimal:
    s = s.strip().lstrip("$").strip()
    s = s.replace("\u200b","").replace("\u202f","")
    s = s.replace(".","").replace(",",".")
    return Decimal(s)


@dataclass
class Prod:
    codigo: str
    nombre: str
    cantidad: Decimal
    precio_unitario: Decimal
    descuento: Decimal
    iva: Optional[Decimal] = None

    @property
    def precio_neto(self) -> Decimal:
        return (self.precio_unitario * (1 - self.descuento / 100)).quantize(Decimal("0.01"))


RE_A = re.compile(
    r"^\[(\d+)\]"
    r"\s+(.+?)"
    r"\s+(\d+,\d{2})"
    r"\s+(?:\S+\s+)?"
    r"(\d{1,3}(?:\.\d{3})*,\d+)"
    r"\s+(\d+,\d{2})"
    r"\s+IVA\s+\$\s*"
    r"(\u200b?-?\d{1,3}(?:\.\d{3})*,\d{2})"
    r"\s*$"
)
RE_IVA_FIN = re.compile(r"(\d+(?:,\d+)?)\s*%\s*$")
STOP = frozenset(["Periodo facturado","Producto Cantidad","Producto  Cantidad",
                  "Base imponible","Total $","Plazo de pago"])


def parsear_pdf_path(path: str) -> tuple[list[Prod], list[dict]]:
    """Parsea desde un archivo en disco — más confiable para PDFs grandes."""
    try:
        import pdfplumber
    except ImportError:
        raise RuntimeError("pdfplumber no instalado")

    lineas: list[str] = []
    with pdfplumber.open(path) as pdf:
        total_pages = len(pdf.pages)
        log.info("PDF abierto: %d páginas", total_pages)
        for pi, page in enumerate(pdf.pages):
            txt = page.extract_text() or ""
            page_lines = [l.strip() for l in txt.splitlines() if l.strip()]
            lineas.extend(page_lines)
            log.info("  Pág %d/%d: %d líneas extraídas", pi+1, total_pages, len(page_lines))

    log.info("Total líneas en PDF: %d", len(lineas))
    return _procesar_lineas(lineas)


def parsear_pdf(pdf_bytes: bytes) -> tuple[list[Prod], list[dict]]:
    """Parsea desde bytes (para tests)."""
    try:
        import pdfplumber
    except ImportError:
        raise RuntimeError("pdfplumber no instalado")

    lineas: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for pi, page in enumerate(pdf.pages):
            txt = page.extract_text() or ""
            page_lines = [l.strip() for l in txt.splitlines() if l.strip()]
            lineas.extend(page_lines)

    return _procesar_lineas(lineas)


def _procesar_lineas(lineas: list[str]) -> tuple[list[Prod], list[dict]]:
    """Lógica central de parseo — separada para reutilizar."""
    productos: list[Prod] = []
    errores:   list[dict] = []
    i = 0
    n = len(lineas)

    while i < n:
        linea = lineas[i]

        if any(linea.startswith(s) for s in STOP):
            i += 1
            continue

        m = RE_A.match(linea)
        if not m:
            i += 1
            continue

        codigo    = m.group(1)
        nombre_p1 = m.group(2).strip()
        qty_s, precio_s, desc_s = m.group(3), m.group(4), m.group(5)

        try:
            cantidad  = to_dec(qty_s)
            precio    = to_dec(precio_s)
            descuento = to_dec(desc_s)
        except InvalidOperation as e:
            errores.append({"linea": linea[:80], "error": str(e)})
            i += 1
            continue

        if precio < 0:
            i += 2
            continue

        nombre_p2 = ""
        iva: Optional[Decimal] = None
        if i + 1 < n:
            lb = lineas[i + 1]
            m_iva = RE_IVA_FIN.search(lb)
            if m_iva:
                try:
                    iva = to_dec(m_iva.group(1))
                except InvalidOperation:
                    pass
                antes = re.sub(r"\b(?:Unidades|KIT|Kits?)\b", "", lb[:m_iva.start()]).strip()
                nombre_p2 = antes
                i += 1

        nombre = (nombre_p1 + (" " + nombre_p2 if nombre_p2 else "")).strip()
        productos.append(Prod(codigo=codigo, nombre=nombre[:250],
                              cantidad=cantidad, precio_unitario=precio,
                              descuento=descuento, iva=iva))
        i += 1

    log.info("Extraidos: %d productos, %d errores", len(productos), len(errores))
    return productos, errores


class ResultadoImportacion(BaseModel):
    message: str
    total_en_pdf: int
    productos_actualizados: int
    productos_insertados: int
    productos_sin_cambio: int
    unidades_ingresadas: int
    codigos_actualizados: List[str]
    codigos_insertados: List[str]
    errores: List[dict]


# ─────────────────────────────────────────────
#  FUNCIÓN UPSERT COMPARTIDA
#  Usa SQL nativo ON CONFLICT para evitar errores de clave duplicada
#  sin importar el estado de la sesión SQLAlchemy
# ─────────────────────────────────────────────
def _upsert_stock_local(db, prod_id: int, local_id: int, cantidad: int):
    """Agrega cantidad a stock_local y recalcula productos.stock como suma."""
    db.execute(
        sql_text("""
            INSERT INTO stock_local (producto_id, local_id, cantidad)
            VALUES (:pid, :lid, :qty)
            ON CONFLICT (producto_id, local_id)
            DO UPDATE SET cantidad = stock_local.cantidad + EXCLUDED.cantidad
        """),
        {"pid": prod_id, "lid": local_id, "qty": cantidad},
    )
    db.execute(
        sql_text("""
            UPDATE productos
            SET stock = (
                SELECT COALESCE(SUM(cantidad), 0)
                FROM stock_local WHERE producto_id = :pid
            )
            WHERE id = :pid
        """),
        {"pid": prod_id},
    )


def upsert_productos(
    productos: list[Prod],
    db: Session,
    usuario_id: int,
    local_id=None,
) -> dict:
    """
    Inserta o actualiza productos usando SQL nativo con ON CONFLICT.
    Retorna conteos: actualizados, insertados, sin_cambio, unidades_ingresadas, errores.
    stock_local se actualiza siempre; productos.stock se recalcula como suma.
    """
    actualizados: list[str] = []
    insertados:   list[str] = []
    sin_cambio:   list[str] = []
    errores_db:   list[dict] = []
    unidades_ingresadas = 0

    # Resolver local destino: el indicado, o el primer local activo
    local_destino = local_id
    if not local_destino:
        row_l = db.execute(
            sql_text("SELECT id FROM locales WHERE activo = TRUE ORDER BY id LIMIT 1")
        ).fetchone()
        if row_l:
            local_destino = row_l[0]

    for p in productos:
        try:
            precio_nuevo = p.precio_neto
            cantidad_int = int(p.cantidad)
            unidades_ingresadas += cantidad_int

            # 1. Buscar si ya existe (incluyendo inactivos para reactivar)
            row = db.execute(
                sql_text("SELECT id, precio FROM productos WHERE codigo_barras = :cod"),
                {"cod": p.codigo}
            ).fetchone()

            if row:
                prod_id, precio_actual = row[0], row[1]

                if abs(float(precio_actual) - float(precio_nuevo)) < 0.01:
                    # Sin cambio de precio — solo stock
                    sin_cambio.append(p.codigo)
                else:
                    # Precio cambió — actualizar precio y registrar historial
                    db.execute(
                        sql_text("""
                            UPDATE productos
                            SET precio = :precio, precio_costo = :precio, activo = true
                            WHERE id = :id
                        """),
                        {"precio": str(precio_nuevo), "id": prod_id}
                    )
                    db.execute(
                        sql_text("""
                            INSERT INTO precio_historial (producto_id, precio_anterior, precio_nuevo, usuario_id)
                            VALUES (:pid, :prev, :nuevo, :uid)
                        """),
                        {"pid": prod_id, "prev": str(precio_actual), "nuevo": str(precio_nuevo), "uid": usuario_id}
                    )
                    actualizados.append(p.codigo)

                # Actualizar stock_local y recalcular global
                if local_destino:
                    _upsert_stock_local(db, prod_id, local_destino, cantidad_int)

            else:
                # Producto nuevo
                db.execute(
                    sql_text("""
                        INSERT INTO productos
                            (nombre, precio, precio_costo, codigo_barras, stock, stock_minimo, activo)
                        VALUES
                            (:nombre, :precio, :precio, :cod, 0, 0, true)
                        ON CONFLICT (codigo_barras) DO UPDATE
                            SET precio       = EXCLUDED.precio,
                                precio_costo = EXCLUDED.precio_costo,
                                activo       = true
                    """),
                    {"nombre": p.nombre, "precio": str(precio_nuevo), "cod": p.codigo}
                )
                # Obtener id del producto recién insertado/existente
                row2 = db.execute(
                    sql_text("SELECT id FROM productos WHERE codigo_barras = :cod"),
                    {"cod": p.codigo}
                ).fetchone()
                if row2 and local_destino:
                    _upsert_stock_local(db, row2[0], local_destino, cantidad_int)
                insertados.append(p.codigo)

        except Exception as e:
            log.exception("Error upsert [%s]", p.codigo)
            errores_db.append({"linea": f"[{p.codigo}] {p.nombre[:60]}", "error": str(e)})

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise e

    return {
        "actualizados": actualizados,
        "insertados":   insertados,
        "sin_cambio":   sin_cambio,
        "errores_db":   errores_db,
        "unidades_ingresadas": unidades_ingresadas,
    }



@router.post("/pdf-factura", response_model=ResultadoImportacion,
             summary="Importar productos desde PDF de factura")
async def importar_pdf_factura(
    file: UploadFile = File(...),
    local_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_gerente),
):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF (.pdf)")

    # Leer TODO el archivo en memoria de forma segura
    chunks = []
    while True:
        chunk = await file.read(1024 * 1024)  # 1MB por vez
        if not chunk:
            break
        chunks.append(chunk)
    contenido = b"".join(chunks)

    if not contenido:
        raise HTTPException(status_code=400, detail="El archivo está vacío")

    log.info("PDF recibido: %s (%d bytes)", file.filename, len(contenido))

    # Escribir a archivo temporal para que pdfplumber lo lea de forma confiable
    import tempfile, os
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(contenido)
            tmp_path = tmp.name
        extraidos, errores_parse = parsear_pdf_path(tmp_path)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        log.exception("Error en parsear_pdf")
        raise HTTPException(status_code=500, detail=f"Error al procesar el PDF: {e}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    if not extraidos:
        return ResultadoImportacion(
            message="No se encontraron productos. ¿Es una factura de Lopez Hnos.?",
            total_en_pdf=0, productos_actualizados=0, productos_insertados=0,
            productos_sin_cambio=0, unidades_ingresadas=0,
            codigos_actualizados=[], codigos_insertados=[], errores=errores_parse,
        )

    try:
        res = upsert_productos(extraidos, db, current_user.id, local_id=local_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al guardar: {e}")

    actualizados        = res["actualizados"]
    insertados          = res["insertados"]
    sin_cambio          = res["sin_cambio"]
    unidades_ingresadas = res["unidades_ingresadas"]
    todos_errores = errores_parse + res["errores_db"]
    msg = (f"Importación completada — "
           f"{len(actualizados)} precios actualizados, "
           f"{len(insertados)} productos nuevos, "
           f"{len(sin_cambio)} sin cambio. "
           f"{unidades_ingresadas} unidades al stock"
           f"{f'. {len(todos_errores)} errores' if todos_errores else ''}.")

    return ResultadoImportacion(
        message=msg,
        total_en_pdf=len(extraidos),
        productos_actualizados=len(actualizados),
        productos_insertados=len(insertados),
        productos_sin_cambio=len(sin_cambio),
        unidades_ingresadas=unidades_ingresadas,
        codigos_actualizados=actualizados,
        codigos_insertados=insertados,
        errores=todos_errores,
    )


# ─────────────────────────────────────────────
#  ENDPOINT: ANALIZAR PÁGINAS SIN IMPORTAR
# ─────────────────────────────────────────────
class InfoPagina(BaseModel):
    pagina: int
    num_productos: int
    tiene_precios: bool
    codigos: List[str]

class ResultadoAnalisis(BaseModel):
    total_paginas: int
    total_productos: int
    paginas: List[InfoPagina]

@router.post("/analizar-paginas", response_model=ResultadoAnalisis,
             summary="Analizar páginas del PDF sin importar")
async def analizar_paginas(
    file: UploadFile = File(...),
    current_user: models.Usuario = Depends(require_gerente),
):
    """Devuelve cuántos productos hay en cada página del PDF sin tocar la base de datos."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF (.pdf)")
    contenido = await file.read()
    if not contenido:
        raise HTTPException(status_code=400, detail="El archivo está vacío")

    try:
        import pdfplumber
    except ImportError:
        raise HTTPException(status_code=500, detail="pdfplumber no instalado")

    try:
        paginas_info: list[InfoPagina] = []
        with pdfplumber.open(io.BytesIO(contenido)) as pdf:
            for pi, page in enumerate(pdf.pages):
                txt = page.extract_text() or ""
                lineas = [l.strip() for l in txt.splitlines() if l.strip()]
                codigos = []
                for linea in lineas:
                    m = RE_A.match(linea)
                    if m:
                        try:
                            precio = to_dec(m.group(4))
                            if precio > 0:
                                codigos.append(m.group(1))
                        except Exception:
                            pass
                paginas_info.append(InfoPagina(
                    pagina=pi + 1,
                    num_productos=len(codigos),
                    tiene_precios=len(codigos) > 0,
                    codigos=codigos,
                ))
        return ResultadoAnalisis(
            total_paginas=len(paginas_info),
            total_productos=sum(p.num_productos for p in paginas_info),
            paginas=paginas_info,
        )
    except Exception as e:
        log.exception("Error en analizar_paginas")
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
#  ENDPOINT: IMPORTAR UNA SOLA PÁGINA
# ─────────────────────────────────────────────
@router.post("/pdf-pagina", response_model=ResultadoImportacion,
             summary="Importar una sola página del PDF")
async def importar_pdf_pagina(
    file: UploadFile = File(...),
    pagina: int = 1,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_gerente),
):
    """
    Extrae e importa solo los productos de la página indicada (1-based).
    Útil para diagnóstico y para importar página por página desde el frontend.
    """
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF (.pdf)")
    contenido = await file.read()
    if not contenido:
        raise HTTPException(status_code=400, detail="El archivo está vacío")

    log.info("Importando página %d del PDF: %s", pagina, file.filename)

    try:
        import pdfplumber
    except ImportError:
        raise HTTPException(status_code=500, detail="pdfplumber no instalado")

    try:
        with pdfplumber.open(io.BytesIO(contenido)) as pdf:
            if pagina < 1 or pagina > len(pdf.pages):
                raise HTTPException(status_code=400, detail=f"Página {pagina} no existe (el PDF tiene {len(pdf.pages)} páginas)")
            page = pdf.pages[pagina - 1]
            txt = page.extract_text() or ""
            lineas = [l.strip() for l in txt.splitlines() if l.strip()]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al leer el PDF: {e}")

    # Parsear solo las líneas de esta página
    from decimal import InvalidOperation as _IE
    productos_pag: list[Prod] = []
    errores_pag:   list[dict] = []
    i = 0
    n = len(lineas)

    while i < n:
        linea = lineas[i]
        if any(linea.startswith(s) for s in STOP):
            i += 1; continue
        m = RE_A.match(linea)
        if not m:
            i += 1; continue
        try:
            cantidad  = to_dec(m.group(3))
            precio    = to_dec(m.group(4))
            descuento = to_dec(m.group(5))
        except (_IE, Exception) as e:
            errores_pag.append({"linea": linea[:80], "error": str(e)})
            i += 1; continue
        if precio < 0:
            i += 2; continue
        nombre_p2 = ""
        iva = None
        if i + 1 < n:
            lb = lineas[i + 1]
            miva = RE_IVA_FIN.search(lb)
            if miva:
                try: iva = to_dec(miva.group(1))
                except Exception: pass
                antes = re.sub(r"\b(?:Unidades|KIT|Kits?)\b", "", lb[:miva.start()]).strip()
                nombre_p2 = antes
                i += 1
        nombre = (m.group(2) + (" " + nombre_p2 if nombre_p2 else "")).strip()
        productos_pag.append(Prod(codigo=m.group(1), nombre=nombre[:250],
                                  cantidad=cantidad, precio_unitario=precio,
                                  descuento=descuento, iva=iva))
        i += 1

    log.info("Página %d: %d productos extraídos, %d errores", pagina, len(productos_pag), len(errores_pag))

    if not productos_pag:
        return ResultadoImportacion(
            message=f"Página {pagina}: sin productos con precio",
            total_en_pdf=0, productos_actualizados=0, productos_insertados=0,
            productos_sin_cambio=0, unidades_ingresadas=0,
            codigos_actualizados=[], codigos_insertados=[], errores=errores_pag,
        )

    try:
        res = upsert_productos(productos_pag, db, current_user.id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al guardar página {pagina}: {e}")

    actualizados        = res["actualizados"]
    insertados          = res["insertados"]
    sin_cambio          = res["sin_cambio"]
    unidades_ingresadas = res["unidades_ingresadas"]
    todos_errores = errores_pag + res["errores_db"]
    msg = (f"Página {pagina}: {len(actualizados)} actualizados, "
           f"{len(insertados)} nuevos, {len(sin_cambio)} sin cambio. "
           f"{unidades_ingresadas} uds.")

    return ResultadoImportacion(
        message=msg,
        total_en_pdf=len(productos_pag),
        productos_actualizados=len(actualizados),
        productos_insertados=len(insertados),
        productos_sin_cambio=len(sin_cambio),
        unidades_ingresadas=unidades_ingresadas,
        codigos_actualizados=actualizados,
        codigos_insertados=insertados,
        errores=todos_errores,
    )


@router.get("/diagnostico", summary="Diagnóstico del parser PDF")
async def diagnostico():
    try:
        import pdfplumber
        v = pdfplumber.__version__
    except ImportError:
        v = "NO INSTALADO"
    return {
        "version_importar": "v5-paginas-2026-03-09",
        "pdfplumber": v,
        "endpoints": ["/pdf-factura", "/pdf-pagina", "/analizar-paginas", "/diagnostico"],
    }


# ─────────────────────────────────────────────
#  ENDPOINT: IMPORTAR FILA DE EXCEL
# ─────────────────────────────────────────────
class ExcelFila(BaseModel):
    sku: str
    nombre: str
    precio_costo: float
    cantidad: int
    local_id: Optional[int] = None

class ExcelFilaResult(BaseModel):
    sku: str
    accion: str   # "insertado" | "actualizado" | "sin_cambio"
    stock_nuevo: int

@router.post("/excel-fila", response_model=ExcelFilaResult,
             summary="Importar una fila del Excel de lista de precios")
def importar_excel_fila(
    fila: ExcelFila,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_gerente),
):
    """
    Recibe un producto del Excel (SKU, nombre, precio lista, cantidad pedida).
    - Si el SKU ya existe: suma stock + actualiza precio si cambió.
    - Si no existe: crea el producto con ese stock.
    Usa ON CONFLICT para evitar duplicados.
    """
    from decimal import Decimal
    precio = Decimal(str(fila.precio_costo)).quantize(Decimal("0.01"))
    sku    = fila.sku.strip()

    # Resolver local destino
    local_efectivo = fila.local_id
    if not local_efectivo:
        row_l = db.execute(
            sql_text("SELECT id FROM locales WHERE activo = TRUE ORDER BY id LIMIT 1")
        ).fetchone()
        if row_l:
            local_efectivo = row_l[0]

    row = db.execute(
        sql_text("SELECT id, precio, stock FROM productos WHERE codigo_barras = :sku"),
        {"sku": sku}
    ).fetchone()

    if row:
        prod_id, precio_actual, stock_actual = row[0], row[1], row[2] or 0
        nuevo_stock = stock_actual + fila.cantidad
        precio_actual_d = Decimal(str(precio_actual)).quantize(Decimal("0.01"))

        if abs(float(precio_actual_d) - float(precio)) < 0.01:
            db.execute(
                sql_text("UPDATE productos SET activo=true WHERE id=:id"),
                {"id": prod_id}
            )
            # Sin cambio de precio — solo stock_local y recalcular global
            if local_efectivo:
                _upsert_stock_local(db, prod_id, local_efectivo, fila.cantidad)
            db.commit()
            # Leer stock actualizado
            nuevo_stock = db.execute(sql_text("SELECT stock FROM productos WHERE id=:id"),{"id":prod_id}).fetchone()[0]
            return ExcelFilaResult(sku=sku, accion="sin_cambio", stock_nuevo=nuevo_stock)
        else:
            # Precio cambió — actualizar precio, stock_local y recalcular global
            db.execute(
                sql_text("""
                    UPDATE productos SET precio=:p, precio_costo=:p, activo=true WHERE id=:id
                """), {"p": str(precio), "id": prod_id}
            )
            db.execute(
                sql_text("""
                    INSERT INTO precio_historial(producto_id,precio_anterior,precio_nuevo,usuario_id)
                    VALUES(:pid,:prev,:nuevo,:uid)
                """), {"pid": prod_id, "prev": str(precio_actual), "nuevo": str(precio), "uid": current_user.id}
            )
            if local_efectivo:
                _upsert_stock_local(db, prod_id, local_efectivo, fila.cantidad)
            db.commit()
            nuevo_stock = db.execute(sql_text("SELECT stock FROM productos WHERE id=:id"),{"id":prod_id}).fetchone()[0]
            return ExcelFilaResult(sku=sku, accion="actualizado", stock_nuevo=nuevo_stock)
    else:
        # Producto nuevo — INSERT sin stock (stock_local lo maneja)
        db.execute(
            sql_text("""
                INSERT INTO productos(nombre,precio,precio_costo,codigo_barras,stock,stock_minimo,activo)
                VALUES(:nom,:p,:p,:sku,0,0,true)
                ON CONFLICT(codigo_barras) DO UPDATE
                  SET precio=EXCLUDED.precio, precio_costo=EXCLUDED.precio_costo, activo=true
            """), {"nom": fila.nombre[:250], "p": str(precio), "sku": sku}
        )
        prod_row2 = db.execute(sql_text("SELECT id FROM productos WHERE codigo_barras=:s"),{"s":sku}).fetchone()
        if prod_row2 and local_efectivo:
            _upsert_stock_local(db, prod_row2[0], local_efectivo, fila.cantidad)
        db.commit()
        nuevo_stock = fila.cantidad if not prod_row2 else db.execute(sql_text("SELECT stock FROM productos WHERE id=:id"),{"id":prod_row2[0]}).fetchone()[0]
        return ExcelFilaResult(sku=sku, accion="insertado", stock_nuevo=nuevo_stock)