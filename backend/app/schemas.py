"""
schemas.py — Schemas Pydantic para MotorRepuesto SSI

Separamos esquemas INTERNOS (con precio_costo) de PÚBLICOS (sin él)
para no exponer márgenes al catálogo público.
"""
from pydantic import BaseModel, field_validator
from decimal import Decimal
from typing import Optional, List
from datetime import datetime, date


# ── CATEGORÍAS ────────────────────────────────────────────────────────────────
class CategoriaBase(BaseModel):
    nombre: str
    descripcion: Optional[str] = None
    icono: Optional[str] = None

class CategoriaCreate(CategoriaBase): pass

class CategoriaOut(CategoriaBase):
    id: int
    creado_en: datetime
    model_config = {"from_attributes": True}


# ── SUBCATEGORÍAS ─────────────────────────────────────────────────────────────
class SubcategoriaBase(BaseModel):
    nombre: str
    categoria_id: int

class SubcategoriaCreate(SubcategoriaBase): pass

class SubcategoriaOut(SubcategoriaBase):
    id: int
    creado_en: datetime
    model_config = {"from_attributes": True}


# ── STOCK POR LOCAL ───────────────────────────────────────────────────────────
class StockLocalOut(BaseModel):
    local_id: int
    cantidad: int
    model_config = {"from_attributes": True}


# ── PRODUCTOS ─────────────────────────────────────────────────────────────────
class ProductoBase(BaseModel):
    nombre: str
    descripcion: Optional[str] = None
    precio: Decimal
    precio_oferta: Optional[Decimal] = None
    imagen_url: Optional[str] = None
    subcategoria_id: Optional[int] = None
    moto_110: bool = False
    moto_150: bool = False
    moto_200: bool = False
    marca: Optional[str] = None
    medida: Optional[str] = None
    codigo_barras: Optional[str] = None
    stock: int = 0
    stock_minimo: int = 0
    activo: bool = True


class ProductoCreate(ProductoBase):
    """Para crear un producto desde el panel (incluye precio_costo y margen)."""
    precio_costo: Optional[Decimal] = None
    margen_individual: Optional[Decimal] = None


class ProductoUpdate(BaseModel):
    """Para PATCH — todos opcionales. margen_individual=0 borra el margen."""
    nombre: Optional[str] = None
    descripcion: Optional[str] = None
    precio: Optional[Decimal] = None
    precio_costo: Optional[Decimal] = None          # costo de compra base
    precio_oferta: Optional[Decimal] = None
    margen_individual: Optional[Decimal] = None     # None=no tocó, 0=borrar, N=activo
    imagen_url: Optional[str] = None
    subcategoria_id: Optional[int] = None
    moto_110: Optional[bool] = None
    moto_150: Optional[bool] = None
    moto_200: Optional[bool] = None
    marca: Optional[str] = None
    medida: Optional[str] = None
    codigo_barras: Optional[str] = None
    stock: Optional[int] = None
    stock_minimo: Optional[int] = None
    activo: Optional[bool] = None


class ProductoOut(ProductoBase):
    """Respuesta completa — incluye precio_costo y margen (solo para usuarios autenticados)."""
    id: int
    precio_costo: Optional[Decimal] = None
    margen_individual: Optional[Decimal] = None
    creado_en: datetime
    actualizado_en: datetime
    model_config = {"from_attributes": True}


class ProductoPublico(ProductoBase):
    """Versión pública — NO incluye precio_costo ni margen."""
    id: int
    model_config = {"from_attributes": True}


class ProductoMinimo(BaseModel):
    """Versión compacta para listas, ventas y búsquedas (autenticado)."""
    id: int
    nombre: str
    precio: Decimal
    precio_costo: Optional[Decimal] = None
    precio_oferta: Optional[Decimal] = None
    margen_individual: Optional[Decimal] = None
    stock: int
    marca: Optional[str] = None
    medida: Optional[str] = None
    codigo_barras: Optional[str] = None
    imagen_url: Optional[str] = None
    model_config = {"from_attributes": True}


# ── PRECIO HISTORIAL ──────────────────────────────────────────────────────────
class PrecioHistorialOut(BaseModel):
    id: int
    producto_id: int
    precio_anterior: Decimal
    precio_nuevo: Decimal
    usuario_id: Optional[int] = None
    creado_en: datetime
    model_config = {"from_attributes": True}


# ── AJUSTE DE STOCK ───────────────────────────────────────────────────────────
class AjusteStockCreate(BaseModel):
    cantidad: int               # positivo=entrada, negativo=salida
    motivo: Optional[str] = None
    local_id: Optional[int] = None


class AjusteStockOut(BaseModel):
    id: int
    producto_id: int
    local_id: Optional[int] = None
    cantidad: int
    stock_antes: int
    stock_despues: int
    motivo: Optional[str] = None
    creado_en: datetime
    model_config = {"from_attributes": True}


# ── LOCALES ───────────────────────────────────────────────────────────────────
class LocalOut(BaseModel):
    id: int
    nombre: str
    direccion: Optional[str] = None
    activo: bool
    model_config = {"from_attributes": True}


# ── USUARIOS ──────────────────────────────────────────────────────────────────
class UsuarioCreate(BaseModel):
    username: str
    password: str
    nombre_real: Optional[str] = None
    rol: str = "vendedor"
    local_id: Optional[int] = None

    @field_validator("rol")
    @classmethod
    def validate_rol(cls, v):
        if v not in ("root", "gerente", "vendedor"):
            raise ValueError("rol debe ser: root, gerente o vendedor")
        return v


class UsuarioUpdate(BaseModel):
    nombre_real: Optional[str] = None
    rol: Optional[str] = None
    local_id: Optional[int] = None
    activo: Optional[bool] = None
    password: Optional[str] = None


class UsuarioOut(BaseModel):
    id: int
    username: str
    nombre_real: Optional[str] = None
    rol: str
    local_id: Optional[int] = None
    activo: bool
    creado_en: datetime
    model_config = {"from_attributes": True}


# ── AUTH ──────────────────────────────────────────────────────────────────────
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    usuario: UsuarioOut


# ── VENTAS ────────────────────────────────────────────────────────────────────
class VentaItemCreate(BaseModel):
    producto_id: int
    cantidad: int


class VentaItemOut(BaseModel):
    id: int
    producto_id: int
    cantidad: int
    precio_unitario: Decimal
    subtotal: Decimal
    model_config = {"from_attributes": True}


class VentaCreate(BaseModel):
    items: List[VentaItemCreate]
    notas: Optional[str] = None


class VentaAnular(BaseModel):
    motivo: Optional[str] = None


class VentaOut(BaseModel):
    id: int
    local_id: int
    usuario_id: Optional[int] = None
    total: Decimal
    notas: Optional[str] = None
    anulada: bool = False
    anulada_motivo: Optional[str] = None
    anulada_en: Optional[datetime] = None
    creado_en: datetime
    items: List[VentaItemOut] = []
    model_config = {"from_attributes": True}


# ── ESTADÍSTICAS ──────────────────────────────────────────────────────────────
class StatsLocal(BaseModel):
    local_id: int
    local_nombre: str
    total_ventas: int
    monto_total: Decimal
    productos_vendidos: int


class StatsGenerales(BaseModel):
    total_productos: int
    productos_sin_stock: int
    productos_stock_bajo: int
    total_ventas_hoy: int
    monto_ventas_hoy: Decimal
    por_local: List[StatsLocal]


class CierreVendedor(BaseModel):
    usuario_id: Optional[int]
    username: Optional[str]
    nombre_real: Optional[str]
    cantidad_ventas: int
    monto_total: Decimal


class CierreCaja(BaseModel):
    fecha: date
    local_id: int
    local_nombre: str
    total_ventas: int
    monto_total: Decimal
    ventas_anuladas: int
    por_vendedor: List[CierreVendedor]


# ── FINANZAS (lógica movida desde frontend) ───────────────────────────────────
class VentaMensual(BaseModel):
    mes: str            # "2026-01"
    total_ventas: int
    monto_total: Decimal
    ganancia_bruta: Decimal


class ProductoTopVentas(BaseModel):
    producto_id: int
    nombre: str
    cantidad_vendida: int
    monto_total: Decimal
    ganancia_bruta: Decimal


class ReporteFinanciero(BaseModel):
    fecha_desde: date
    fecha_hasta: date
    monto_ventas: Decimal
    costo_total: Decimal
    ganancia_bruta: Decimal
    margen_promedio_pct: Decimal
    cantidad_ventas: int
    por_mes: List[VentaMensual]
    top_productos: List[ProductoTopVentas]