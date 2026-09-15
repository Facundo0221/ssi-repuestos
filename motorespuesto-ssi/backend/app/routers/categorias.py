from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app import models, schemas
from app.auth import require_gerente

router = APIRouter(prefix="/categorias", tags=["Categorías"])


@router.get("/", response_model=list[schemas.CategoriaOut])
def listar_categorias(db: Session = Depends(get_db)):
    return db.query(models.Categoria).all()


@router.get("/{categoria_id}/subcategorias", response_model=list[schemas.SubcategoriaOut])
def listar_subcategorias(categoria_id: int, db: Session = Depends(get_db)):
    categoria = db.query(models.Categoria).filter(models.Categoria.id == categoria_id).first()
    if not categoria:
        raise HTTPException(status_code=404, detail="Categoría no encontrada")
    return categoria.subcategorias


@router.post("/", response_model=schemas.CategoriaOut, status_code=201)
def crear_categoria(
    data: schemas.CategoriaCreate,
    db: Session = Depends(get_db),
    _=Depends(require_gerente),
):
    nueva = models.Categoria(**data.model_dump())
    db.add(nueva)
    db.commit()
    db.refresh(nueva)
    return nueva


@router.post("/subcategorias", response_model=schemas.SubcategoriaOut, status_code=201)
def crear_subcategoria(
    data: schemas.SubcategoriaCreate,
    db: Session = Depends(get_db),
    _=Depends(require_gerente),
):
    nueva = models.Subcategoria(**data.model_dump())
    db.add(nueva)
    db.commit()
    db.refresh(nueva)
    return nueva