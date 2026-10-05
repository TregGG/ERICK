from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models.user import User
from ..schemas import RegisterRequest, PhoneRequest, VerifyOTPRequest, TokenResponse
from ..auth.otp import generate_otp, store_otp, verify_otp, send_otp
from ..auth.jwt import create_token
import uuid

router = APIRouter(prefix="/api/auth", tags=["auth"])

@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(request: RegisterRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).filter((User.phone == request.phone) | (User.username == request.username)))
    existing_user = result.scalars().first()
    if existing_user:
        raise HTTPException(status_code=400, detail="User with this phone or username already exists")
    
    new_user = User(username=request.username, phone=request.phone, role=request.role)
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return {"user_id": str(new_user.id)}

@router.post("/request-otp")
async def request_otp(request: PhoneRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).filter(User.phone == request.phone))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    otp = generate_otp()
    store_otp(request.phone, otp)
    send_otp(request.phone, otp)
    return {"message": "OTP sent successfully"}

@router.post("/verify-otp", response_model=TokenResponse)
async def verify_otp_endpoint(request: VerifyOTPRequest, db: AsyncSession = Depends(get_db)):
    if not verify_otp(request.phone, request.otp):
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")
    
    result = await db.execute(select(User).filter(User.phone == request.phone))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if not user.is_verified:
        user.is_verified = True
        await db.commit()
    
    token = create_token(str(user.id), user.role)
    return {"access_token": token, "token_type": "bearer"}

@router.post("/login")
async def login(request: PhoneRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).filter(User.phone == request.phone))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not registered")
    
    otp = generate_otp()
    store_otp(request.phone, otp)
    send_otp(request.phone, otp)
    return {"message": "OTP sent successfully for login"}
