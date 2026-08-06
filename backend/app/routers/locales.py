from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app import models, schemas
from app.auth import require_root

router = APIRouter(prefix="/locales", tags=["Locales"])


@router.get("/", response_model=list[schemas.LocalOut])
def listar_locales(db: Session = Depends(get_db)):
    return db.query(models.Local).filter(models.Local.activo == True).all()