"""
models.py — Modelos SQLAlchemy para MotorRepuesto SSI
"""
from sqlalchemy import (
    Column, Integer, String, Numeric, Boolean,
    ForeignKey, DateTime, Text, UniqueConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


# ── LOCALES ───────────────────────────────────────────────────────────────────
class Local(Base):
    __tablename__ = "locales"

    id         = Column(Integer, primary_key=True, index=True)
    nombre     = Column(String(100), nullable=False)
    direccion  = Column(String(255))
    activo     = Column(Boolean, default=True)
    creado_en  = Column(DateTime(timezone=True), server_default=func.now())

    usuarios   = relationship("Usuario",  back_populates="local")
    ventas     = relationship("Venta",    back_populates="local")
    stocks     = relationship("StockLocal", back_populates="local")


# ── USUARIOS ──────────────────────────────────────────────────────────────────
class Usuario(Base):
    __tablename__ = "usuarios"

    id             = Column(Integer, primary_key=True, index=True)
    username       = Column(String(50), unique=True, nullable=False, index=True)
    password_hash  = Column(String(255), nullable=False)
    nombre_real    = Column(String(100))
    rol            = Column(String(20), default="vendedor")   # root | gerente | vendedor
    local_id       = Column(Integer, ForeignKey("locales.id", ondelete="SET NULL"), nullable=True)
    activo         = Column(Boolean, default=True)
    creado_en      = Column(DateTime(timezone=True), server_default=func.now())

    local          = relationship("Local",   back_populates="usuarios")
    ventas         = relationship("Venta",   back_populates="usuario")
    ajustes_stock  = relationship("AjusteStock", back_populates="usuario")
    historial_precios = relationship("PrecioHistorial", back_populates="usuario")


# ── CATEGORÍAS ────────────────────────────────────────────────────────────────
class Categoria(Base):
    __tablename__ = "categorias"

    id           = Column(Integer, primary_key=True, index=True)
    nombre       = Column(String(100), nullable=False)
    descripcion  = Column(Text)
    icono        = Column(String(10))
    creado_en    = Column(DateTime(timezone=True), server_default=func.now())

    subcategorias = relationship("Subcategoria", back_populates="categoria",
                                 cascade="all, delete-orphan")


class Subcategoria(Base):
    __tablename__ = "subcategorias"

    id           = Column(Integer, primary_key=True, index=True)
    nombre       = Column(String(100), nullable=False)
    categoria_id = Column(Integer, ForeignKey("categorias.id", ondelete="CASCADE"), nullable=False)
    creado_en    = Column(DateTime(timezone=True), server_default=func.now())

    categoria    = relationship("Categoria", back_populates="subcategorias")
    productos    = relationship("Producto",  back_populates="subcategoria")


# ── PRODUCTOS ─────────────────────────────────────────────────────────────────
class Producto(Base):
    __tablename__ = "productos"

    id                = Column(Integer, primary_key=True, index=True)
    nombre            = Column(String(250), nullable=False)
    descripcion       = Column(Text)

    # Precios
    precio            = Column(Numeric(10, 2), nullable=False)   # precio de VENTA actual
    precio_costo      = Column(Numeric(10, 2))                   # precio de compra/importación
    precio_oferta     = Column(Numeric(10, 2))                   # precio especial temporal
    margen_individual = Column(Numeric(5, 2))                    # % margen aplicado (0 = sin margen)

    # Imagen
    imagen_url        = Column(String(500))

    # Clasificación
    subcategoria_id   = Column(Integer, ForeignKey("subcategorias.id", ondelete="SET NULL"), nullable=True)
    moto_110          = Column(Boolean, default=False)
    moto_150          = Column(Boolean, default=False)
    moto_200          = Column(Boolean, default=False)
    marca             = Column(String(100))
    medida            = Column(String(50))
    codigo_barras     = Column(String(100), unique=True)

    # Stock global (suma de todos los locales — se mantiene sincronizado)
    stock             = Column(Integer, default=0)
    stock_minimo      = Column(Integer, default=0)

    activo            = Column(Boolean, default=True)
    creado_en         = Column(DateTime(timezone=True), server_default=func.now())
    actualizado_en    = Column(DateTime(timezone=True), server_default=func.now(),
                               onupdate=func.now())

    subcategoria      = relationship("Subcategoria",    back_populates="productos")
    venta_items       = relationship("VentaItem",       back_populates="producto")
    historial_precios = relationship("PrecioHistorial", back_populates="producto",
                                     cascade="all, delete-orphan")
    stocks_locales    = relationship("StockLocal",      back_populates="producto",
                                     cascade="all, delete-orphan")
    ajustes_stock     = relationship("AjusteStock",     back_populates="producto",
                                     cascade="all, delete-orphan")


# ── STOCK POR LOCAL ───────────────────────────────────────────────────────────
class StockLocal(Base):
    """
    Stock de un producto en un local específico.
    La columna productos.stock es la suma de todos los StockLocal
    y se mantiene sincronizada en cada operación.
    """
    __tablename__ = "stock_local"
    __table_args__ = (UniqueConstraint("producto_id", "local_id"),)

    id          = Column(Integer, primary_key=True, index=True)
    producto_id = Column(Integer, ForeignKey("productos.id", ondelete="CASCADE"), nullable=False)
    local_id    = Column(Integer, ForeignKey("locales.id",   ondelete="CASCADE"), nullable=False)
    cantidad    = Column(Integer, default=0, nullable=False)

    producto    = relationship("Producto", back_populates="stocks_locales")
    local       = relationship("Local",    back_populates="stocks")


# ── AJUSTE DE STOCK ───────────────────────────────────────────────────────────
class AjusteStock(Base):
    """Auditoría de cada movimiento manual de stock."""
    __tablename__ = "ajuste_stock"

    id            = Column(Integer, primary_key=True, index=True)
    producto_id   = Column(Integer, ForeignKey("productos.id", ondelete="CASCADE"), nullable=False)
    local_id      = Column(Integer, ForeignKey("locales.id",   ondelete="SET NULL"), nullable=True)
    usuario_id    = Column(Integer, ForeignKey("usuarios.id",  ondelete="SET NULL"), nullable=True)
    cantidad      = Column(Integer, nullable=False)      # positivo=entrada, negativo=salida
    stock_antes   = Column(Integer, nullable=False)
    stock_despues = Column(Integer, nullable=False)
    motivo        = Column(String(255))
    creado_en     = Column(DateTime(timezone=True), server_default=func.now())

    producto      = relationship("Producto", back_populates="ajustes_stock")
    local         = relationship("Local")
    usuario       = relationship("Usuario",  back_populates="ajustes_stock")


# ── PRECIO HISTORIAL ──────────────────────────────────────────────────────────
class PrecioHistorial(Base):
    """Registro de cada cambio de precio para auditoría."""
    __tablename__ = "precio_historial"

    id              = Column(Integer, primary_key=True, index=True)
    producto_id     = Column(Integer, ForeignKey("productos.id", ondelete="CASCADE"), nullable=False)
    usuario_id      = Column(Integer, ForeignKey("usuarios.id",  ondelete="SET NULL"), nullable=True)
    precio_anterior = Column(Numeric(10, 2), nullable=False)
    precio_nuevo    = Column(Numeric(10, 2), nullable=False)
    creado_en       = Column(DateTime(timezone=True), server_default=func.now())

    producto        = relationship("Producto", back_populates="historial_precios")
    usuario         = relationship("Usuario",  back_populates="historial_precios")


# ── VENTAS ────────────────────────────────────────────────────────────────────
class Venta(Base):
    __tablename__ = "ventas"

    id             = Column(Integer, primary_key=True, index=True)
    local_id       = Column(Integer, ForeignKey("locales.id",   ondelete="RESTRICT"), nullable=False)
    usuario_id     = Column(Integer, ForeignKey("usuarios.id",  ondelete="SET NULL"), nullable=True)
    total          = Column(Numeric(10, 2), nullable=False)
    notas          = Column(Text)
    anulada        = Column(Boolean, default=False)
    anulada_motivo = Column(String(255))
    anulada_en     = Column(DateTime(timezone=True))
    creado_en      = Column(DateTime(timezone=True), server_default=func.now())

    local          = relationship("Local",    back_populates="ventas")
    usuario        = relationship("Usuario",  back_populates="ventas")
    items          = relationship("VentaItem", back_populates="venta",
                                  cascade="all, delete-orphan")


class VentaItem(Base):
    __tablename__ = "venta_items"

    id              = Column(Integer, primary_key=True, index=True)
    venta_id        = Column(Integer, ForeignKey("ventas.id",    ondelete="CASCADE"), nullable=False)
    producto_id     = Column(Integer, ForeignKey("productos.id", ondelete="RESTRICT"), nullable=False)
    cantidad        = Column(Integer, nullable=False)
    precio_unitario = Column(Numeric(10, 2), nullable=False)
    subtotal        = Column(Numeric(10, 2), nullable=False)

    venta           = relationship("Venta",    back_populates="items")
    producto        = relationship("Producto", back_populates="venta_items")