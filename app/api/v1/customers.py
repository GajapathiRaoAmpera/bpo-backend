from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core import audit
from app.core.db import get_db
from app.core.rbac import Access, Screen, require
from app.models import Customer, Policy
from app.models.enums import AuditAction
from app.schemas.customer import CustomerDetail, CustomerPage, CustomerRow, PolicyOut, customer_fields

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=CustomerPage)
def list_customers(
    q: str | None = Query(None, max_length=80, description="Name, phone, customer code or policy no."),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    access: Access = Depends(require(Screen.customers)),
    db: Session = Depends(get_db),
) -> CustomerPage:
    stmt = select(Customer)
    if q:
        like = f"%{q.strip()}%"
        conds = [Customer.full_name.ilike(like), Customer.customer_code.ilike(like),
                 Customer.id.in_(select(Policy.customer_id).where(Policy.policy_no.ilike(like)))]
        if not access.masked:  # masked roles must not be able to probe by phone number
            conds.append(Customer.phone.ilike(like))
        stmt = stmt.where(or_(*conds))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.options(selectinload(Customer.policies)).order_by(Customer.full_name)
                      .offset((page - 1) * page_size).limit(page_size)).all()
    items = [CustomerRow(**customer_fields(c, access.masked), policy_count=len(c.policies),
                         next_renewal=min((p.renewal_date for p in c.policies), default=None))
             for c in rows]
    return CustomerPage(items=items, total=total, page=page, page_size=page_size)


@router.get("/{customer_id}", response_model=CustomerDetail)
def get_customer(customer_id: int, request: Request, access: Access = Depends(require(Screen.customers)),
                 db: Session = Depends(get_db)) -> CustomerDetail:
    c = db.get(Customer, customer_id, options=[selectinload(Customer.policies)])
    if not c:
        raise HTTPException(404, "Customer not found")
    audit.record(db, request, AuditAction.view_customer, entity_type="customer", entity_id=c.id,
                 detail={"customer_code": c.customer_code, "masked": access.masked})
    return CustomerDetail(**customer_fields(c, access.masked),
                          policies=[PolicyOut.model_validate(p) for p in c.policies])
