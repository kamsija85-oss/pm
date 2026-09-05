import sys
import io

# 콘솔 및 기본 인코딩을 UTF-8로 강제 설정
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import os
import uuid
from datetime import datetime
from fastapi import FastAPI, File, Form, Depends, UploadFile, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session

# 구글 제미나이 라이브러리 임포트
from google import genai
from google.genai import types

# 1. SQLite DB 설정
SQLALCHEMY_DATABASE_URL = "sqlite:///./inspector.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    location = Column(String)
    contractor = Column(String)
    logs = relationship("Log", back_populates="project", cascade="all, delete-orphan")

class Log(Base):
    __tablename__ = "logs"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    date = Column(String)
    work_content = Column(String)
    image_path = Column(String)
    ai_result = Column(Text)
    project = relationship("Project", back_populates="logs")

Base.metadata.create_all(bind=engine)

app = FastAPI()

os.makedirs("static/uploads", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# 환경 변수에서 API 키를 불러와 클라이언트 초기화
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/", response_class=HTMLResponse)
def read_index(request: Request, db: Session = Depends(get_db)):
    projects = db.query(Project).all()
    return templates.TemplateResponse("index.html", {"request": request, "projects": projects})

@app.post("/projects/create")
def create_project(
    name: str = Form(...),
    location: str = Form(...),
    contractor: str = Form(...),
    db: Session = Depends(get_db)
):
    new_project = Project(name=name, location=location, contractor=contractor)
    db.add(new_project)
    db.commit()
    return RedirectResponse(url="/", status_code=303)

@app.get("/projects/{project_id}", response_class=HTMLResponse)
def read_project_detail(request: Request, project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="공사를 찾을 수 없습니다.")
    return templates.TemplateResponse("detail.html", {"request": request, "project": project})

@app.post("/projects/{project_id}/logs", response_class=HTMLResponse)
async def create_log(
    request: Request,
    project_id: int,
    work_content: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="공사를 찾을 수 없습니다.")

    # 안전한 파일명 생성 (인코딩 에러 방지)
    file_bytes = await file.read()
    ext = os.path.splitext(file.filename)[1]
    filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"
    file_path = os.path.join("static", "uploads", filename)
    with open(file_path, "wb") as f:
        f.write(file_bytes)

    # 제미나이 프롬프트 구성
    system_instruction = (
        "당신은 대한민국 토목직 공무원을 돕는 30년 차 베테랑 토목시공기술사 및 안전관리 자문관입니다. "
        "업로드된 현장 사진과 공사 정보를 바탕으로 다음 세 가지를 마크다운 형식으로 명확하고 간결하게 도출해주세요.\n\n"
        "1. [🚨 위험요소 및 안전 체크포인트]\n"
        "- 산업안전보건기준 및 건설기준(KCS, KDS) 관점에서 사진 속 위험 요소를 찾아냅니다.\n"
        "- 초보 주무관이 현장에서 즉시 지적하거나 조치해야 할 행동 지침을 2~3가지로 요약해주세요.\n\n"
        "2. [📝 금일 작업 내용 (일지용)]\n"
        "- 공사감독일지에 바로 쓸 수 있는 전문적이고 간결한 행정 용어의 작업 내용 2줄을 작성해주세요.\n\n"
        "3. [📌 특기사항 및 지시사항 (일지용)]\n"
        "- 현장 시공 상태 점검 결과 및 시공사(감리단)에 대한 지시사항 형태로 들어갈 문구를 1줄 작성해주세요."
    )

    try:
        # 제미나이 이미지 전송용 객체 생성
        image_part = types.Part.from_bytes(
            data=file_bytes,
            mime_type=file.content_type or "image/jpeg",
        )

        user_prompt = f"공사명: {project.name}\n작업키워드: {work_content}\n이 현장 사진을 분석해줘."

        # Gemini API 호출 (최신 gemini-3.6-flash 모델 및 AFC 경고 비활성화 적용)
        response = client.models.generate_content(
            model='gemini-3.6-flash',
            contents=[image_part, user_prompt],
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.3,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                )
            )
        )
        ai_result = response.text
    except Exception as e:
        import traceback
        traceback.print_exc()
        ai_result = f"제미나이 AI 분석 중 오류 발생: {str(e)}"

    # DB에 누적 저장
    new_log = Log(
        project_id=project.id,
        date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        work_content=work_content,
        image_path=f"/static/uploads/{filename}",
        ai_result=ai_result
    )
    db.add(new_log)
    db.commit()

    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
    
@app.post("/logs/{log_id}/delete")
def delete_log(log_id: int, db: Session = Depends(get_db)):
    log = db.query(Log).filter(Log.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="해당 일지를 찾을 수 없습니다.")
    
    project_id = log.project_id

    # 1. 서버에 저장된 실제 이미지 파일 삭제 (파일이 존재하는 경우)
    if log.image_path:
        # log.image_path는 "/static/uploads/파일명" 형태이므로 실제 로컬 경로로 변환
        file_system_path = log.image_path.lstrip("/") # 앞의 '/' 제거
        if os.path.exists(file_system_path):
            try:
                os.remove(file_system_path)
            except Exception as e:
                print(f"이미지 파일 삭제 실패: {e}")

    # 2. 데이터베이스에서 로그 레코드 삭제
    db.delete(log)
    db.commit()

    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)

@app.post("/projects/{project_id}/delete")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="공사를 찾을 수 없습니다.")
    
    # 해당 공사에 속한 모든 일지의 실제 이미지 파일 삭제
    for log in project.logs:
        if log.image_path:
            file_system_path = log.image_path.lstrip("/")
            if os.path.exists(file_system_path):
                try:
                    os.remove(file_system_path)
                except Exception as e:
                    print(f"이미지 파일 삭제 실패: {e}")

    # 공사 삭제 (cascade 설정에 의해 연관된 Log 데이터도 DB에서 함께 삭제됨)
    db.delete(project)
    db.commit()

    return RedirectResponse(url="/", status_code=303)