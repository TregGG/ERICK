from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models.user import User
from ..schemas import RegisterRequest, PhoneRequest, VerifyOTPRequest, TokenResponse, ProfileUpdate, UserProfile
from ..auth.otp import generate_otp, store_otp, verify_otp, send_otp
from ..auth.jwt import create_token, get_current_user
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
        # Auto-create user if not found so login works immediately
        base_name = f"{request.role or 'user'}_{request.phone[-4:]}"
        check_name = await db.execute(select(User).filter(User.username == base_name))
        if check_name.scalars().first():
            base_name = f"{base_name}_{uuid.uuid4().hex[:4]}"
        user = User(username=base_name, phone=request.phone, role=request.role or "passenger")
        db.add(user)
        await db.commit()
        await db.refresh(user)
    
    otp = generate_otp()
    store_otp(request.phone, otp)
    send_otp(request.phone, otp)
    return {"message": "OTP sent successfully", "debug_otp": otp}

@router.post("/verify-otp", response_model=TokenResponse)
async def verify_otp_endpoint(request: VerifyOTPRequest, db: AsyncSession = Depends(get_db)):
    if not verify_otp(request.phone, request.otp):
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")
    
    result = await db.execute(select(User).filter(User.phone == request.phone))
    user = result.scalars().first()
    if not user:
        base_name = f"{request.role or 'user'}_{request.phone[-4:]}"
        user = User(username=base_name, phone=request.phone, role=request.role or "passenger", is_verified=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)
    else:
        changed = False
        if not user.is_verified:
            user.is_verified = True
            changed = True
        if request.role and user.role != request.role:
            user.role = request.role
            changed = True
        if changed:
            await db.commit()
            await db.refresh(user)
    
    token = create_token(str(user.id), user.role)
    return {"access_token": token, "token": token, "token_type": "bearer"}

@router.post("/login")
async def login(request: PhoneRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).filter(User.phone == request.phone))
    user = result.scalars().first()
    if not user:
        base_name = f"{request.role or 'user'}_{request.phone[-4:]}"
        user = User(username=base_name, phone=request.phone, role=request.role or "passenger")
        db.add(user)
        await db.commit()
        await db.refresh(user)
    
    otp = generate_otp()
    store_otp(request.phone, otp)
    send_otp(request.phone, otp)
    return {"message": "OTP sent successfully for login", "debug_otp": otp}

@router.get("/me", response_model=UserProfile)
async def get_me(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).filter(User.id == current_user["user_id"]))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@router.put("/profile", response_model=UserProfile)
@router.post("/profile", response_model=UserProfile)
async def update_profile(request: ProfileUpdate, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).filter(User.id == current_user["user_id"]))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.name = request.name.strip()
    await db.commit()
    await db.refresh(user)
    return user
