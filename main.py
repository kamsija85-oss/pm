import sys
import io

# 콘솔 및 기본 인코딩을 UTF-8로 강제 설정
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import os
import uuid
import json
import urllib.request
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

# API 키 환경 변수에서 로드
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

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
    weather_info = Column(String)  # 기상 정보 저장용
    project = relationship("Project", back_populates="logs")

Base.metadata.create_all(bind=engine)

app = FastAPI()

os.makedirs("static/uploads", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# 클라이언트 초기화
client = genai.Client(api_key=GEMINI_API_KEY)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_weather_info(location_name: str):
    try:
        lat, lon = 35.2413, 129.2249  # 기본 좌표 (기장군청)

        if location_name:
            loc_lower = location_name.replace(" ", "")
            if "정관" in loc_lower:
                lat, lon = 35.3214, 129.1767 
            elif "장안" in loc_lower:
                lat, lon = 35.3331, 129.2797 
            elif "일광" in loc_lower:
                lat, lon = 35.2577, 129.2294 
            elif "철마" in loc_lower:
                lat, lon = 35.2819, 129.1558 
            elif "기장" in loc_lower:
                lat, lon = 35.2413, 129.2249 
            elif "부산" in loc_lower or "해운대" in loc_lower:
                lat, lon = 35.1796, 129.0756 

        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m&daily=weathercode,precipitation_probability_max,temperature_2m_min,temperature_2m_max&timezone=Asia%2FSeoul"
        
        # 렌더 서버 환경에서 더 안정적인 urllib 표준 라이브러리 및 타임아웃 7초 적용
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        
        with urllib.request.urlopen(req, timeout=7) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        
        current = data.get("current", {})
        temp = current.get("temperature_2m", "정보 없음")
        humidity = current.get("relative_humidity_2m", "정보 없음")
        precip = current.get("precipitation", 0)
        wind = current.get("wind_speed_10m", 0)
        
        weather_desc = f"기온: {temp}°C | 습도: {humidity}% | 강수량: {precip}mm | 풍속: {wind}m/s"
        
        def parse_weather_code(code, rain_prob):
            if rain_prob < 30:
                if code == 0: return "☀️ 맑음"
                elif code in [1, 2]: return "⛅ 구름 많음"
                else: return "☁️ 흐림"

            if code == 0: return "☀️ 맑음"
            elif code in [1, 2, 3]: return "⛅ 구름 많음"
            elif code in [45, 48]: return "🌫️ 안개"
            elif code in [51, 53, 55, 56, 57]: return "🌧️ 이슬비"
            elif code in [61, 63, 65, 66, 67]: return "☔ 비"
            elif code in [71, 73, 75, 77]: return "❄️ 눈"
            elif code in [95, 96, 99]: return "⛈️ 뇌우"
            else: return "☁️ 흐림"

        daily = data.get("daily", {})
        dates = daily.get("time", [])
        weathercodes = daily.get("weathercode", [])
        rain_probs = daily.get("precipitation_probability_max", [])
        t_mins = daily.get("temperature_2m_min", [])
        t_maxs = daily.get("temperature_2m_max", [])
        
        forecast_list = []
        for i in range(len(dates)):
            code = weathercodes[i] if i < len(weathercodes) else 0
            r_prob = rain_probs[i] if i < len(rain_probs) else 0
            t_min = t_mins[i] if i < len(t_mins) else 0
            t_max = t_maxs[i] if i < len(t_maxs) else 0
            forecast_list.append({
                "date": dates[i],
                "status": parse_weather_code(code, r_prob),
                "rain_prob": r_prob,
                "temp_min": round(t_min, 1),
                "temp_max": round(t_max, 1)
            })

        today_info = forecast_list[0] if forecast_list else None
        weekly_forecast = forecast_list[1:6] if len(forecast_list) > 1 else []

        alerts = []
        if precip > 0:
            alerts.append("🚨 [우천 주의] 강우 대비 사면 보호 필요")
        if wind > 10:
            alerts.append("🚨 [강풍 주의] 자재 결박 점검 필요")
        if isinstance(temp, (int, float)) and temp >= 33:
            alerts.append("🚨 [폭염 주의] 근로자 휴식 부여 필요")
            
        alert_text = " / ".join(alerts) if alerts else "기상 특보 없음"
        
        return {
            "current_text": weather_desc,
            "alert_text": alert_text,
            "today": today_info,
            "forecast": weekly_forecast
        }
    except Exception as e:
        print(f"⚠️ 날씨 조회 중 에러 발생: {str(e)}")
        return {
            "current_text": "기상 정보 조회 불가",
            "alert_text": "",
            "today": None,
            "forecast": []
        }

@app.get("/", response_class=HTMLResponse)
def read_index(request: Request, db: Session = Depends(get_db)):
    projects = db.query(Project).all()
    project_weather = {}
    for p in projects:
        project_weather[p.id] = get_weather_info(p.location)
    return templates.TemplateResponse(request, "index.html", {
        "projects": projects,
        "project_weather": project_weather
    })

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
    current_weather = get_weather_info(project.location)
    return templates.TemplateResponse(request, "detail.html", {
        "project": project,
        "weather_info": current_weather
    })

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

    file_bytes = await file.read()
    ext = os.path.splitext(file.filename)[1]
    filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"
    file_path = os.path.join("static", "uploads", filename)
    with open(file_path, "wb") as f:
        f.write(file_bytes)

    current_weather = get_weather_info(project.location)

    weather_summary_str = "기상 정보 없음"
    if current_weather and current_weather.get("today"):
        t = current_weather["today"]
        weather_summary_str = f"{t['status']} | 최저 {int(t['temp_min'])}°C / 최고 {int(t['temp_max'])}°C (강수확률: {t['rain_prob']}%)"

    system_instruction = (
        "당신은 대한민국 토목직 공무원을 돕는 30년 차 베테랑 토목시공기술사 및 안전관리 자문관입니다. "
        "업로드된 현장 사진, 공사 정보, 당일 기상 정보를 바탕으로 다음 세 가지를 마크다운 형식으로 명확하고 간결하게 도출해주세요.\n\n"
        "1. [🚨 위험요소 및 안전 체크포인트]\n"
        "- 산업안전보건기준 및 건설기준(KCS, KDS) 관점 및 당일 기상 상황(비, 강풍, 폭염 등)을 연계하여 위험 요소를 찾아냅니다.\n"
        "- 초보 주무관이 현장에서 즉시 지적하거나 조치해야 할 행동 지침을 2~3가지로 요약해주세요.\n\n"
        "2. [📝 금일 작업 내용 (일지용)]\n"
        "- 공사감독일지에 바로 쓸 수 있는 전문적이고 간결한 행정 용어의 작업 내용 2줄을 작성해주세요.\n\n"
        "3. [📌 특기사항 및 지시사항 (일지용)]\n"
        "- 현장 시공 상태 점검 결과 및 기상 악화(우천 예보 포함) 대비 시공사(감리단)에 대한 지시사항 형태로 들어갈 문구를 2줄 작성해주세요."
    )

    try:
        image_part = types.Part.from_bytes(
            data=file_bytes,
            mime_type=file.content_type or "image/jpeg",
        )

        user_prompt = f"공사명: {project.name}\n위치: {project.location}\n작업키워드: {work_content}\n당일 기상정보: {current_weather}\n이 현장 사진과 기상 상황을 종합해 분석해줘."

        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
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

    new_log = Log(
        project_id=project.id,
        date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        work_content=work_content,
        image_path=f"/static/uploads/{filename}",
        ai_result=ai_result,
        weather_info=weather_summary_str  
    )
    db.add(new_log)
    db.commit()

    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)

@app.post("/logs/{log_id}/update")
def update_log_result(
    log_id: int,
    ai_result: str = Form(...),
    db: Session = Depends(get_db)
):
    log = db.query(Log).filter(Log.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="일지를 찾을 수 없습니다.")
    
    log.ai_result = ai_result
    db.commit()

    return RedirectResponse(url=f"/projects/{log.project_id}", status_code=303)

@app.post("/logs/{log_id}/delete")
def delete_log(log_id: int, db: Session = Depends(get_db)):
    log = db.query(Log).filter(Log.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="해당 일지를 찾을 수 없습니다.")
    
    project_id = log.project_id

    if log.image_path:
        file_system_path = log.image_path.lstrip("/") 
        if os.path.exists(file_system_path):
            try:
                os.remove(file_system_path)
            except Exception as e:
                print(f"이미지 파일 삭제 실패: {e}")

    db.delete(log)
    db.commit()

    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)

@app.post("/projects/{project_id}/delete")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="공사를 찾을 수 없습니다.")
    
    for log in project.logs:
        if log.image_path:
            file_system_path = log.image_path.lstrip("/")
            if os.path.exists(file_system_path):
                try:
                    os.remove(file_system_path)
                except Exception as e:
                    print(f"이미지 파일 삭제 실패: {e}")

    db.delete(project)
    db.commit()

    return RedirectResponse(url="/", status_code=303)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)