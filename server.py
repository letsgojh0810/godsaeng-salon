import os
import re
import json
import base64
import sqlite3
from datetime import datetime

import httpx
import google.generativeai as genai
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

load_dotenv()

port = int(os.getenv("PORT", "8000"))

mcp = FastMCP("godsaeng-salon", host="0.0.0.0", port=port)


def get_model(model_name: str = "gemini-2.0-flash"):
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY is not configured.")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(model_name)


def extract_json(text: str) -> dict:
    text = text.strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return json.loads(match.group())
    return json.loads(text)


DB_PATH = "scores.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scores (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT    NOT NULL,
                room_id     TEXT    NOT NULL,
                date        TEXT    NOT NULL,
                godsaeng    INTEGER DEFAULT 0,
                dopamine    INTEGER DEFAULT 0,
                activity    TEXT,
                comment     TEXT,
                created_at  TEXT    DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


init_db()


def _fetch_scores(room_id: str, date: str) -> list:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT user_id,
                   SUM(godsaeng) AS godsaeng,
                   SUM(dopamine) AS dopamine,
                   GROUP_CONCAT(activity, ', ') AS activities
            FROM   scores
            WHERE  room_id = ? AND date = ?
            GROUP  BY user_id
            ORDER  BY godsaeng DESC
            """,
            (room_id, date),
        ).fetchall()
    return [dict(r) for r in rows]


@mcp.tool(
    annotations=ToolAnnotations(
        title="Analyze Lifestyle Photo",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
)
def analyze_photo(image_url: str, user_id: str, room_id: str) -> str:
    """Analyzes a daily lifestyle photo using GodSaeng Salon(갓생살롱) and measures the GodSaeng(갓생) productivity index and Dopamine(도파민) index. Stores the score in the database and returns a formatted result.

    Args:
        image_url: URL of the photo to analyze
        user_id:   User ID who uploaded the photo (e.g., minjun)
        room_id:   Group chat room ID (e.g., room_abc123)
    """
    resp = httpx.get(image_url, follow_redirects=True, timeout=30)
    resp.raise_for_status()

    media_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]
    image_b64 = base64.standard_b64encode(resp.content).decode("utf-8")

    model = get_model()
    response = model.generate_content([
        {"inline_data": {"mime_type": media_type, "data": image_b64}},
        (
            "이 사진을 분석하여 아래 JSON 형식으로만 응답하세요. "
            "다른 텍스트는 절대 포함하지 마세요.\n\n"
            "{\n"
            '  "activity": "카공/헬스/독서/업무/침대눕방/게임/배달음식/기타 중 하나",\n'
            '  "godsaeng": 0~30 사이 정수,\n'
            '  "dopamine": 0~30 사이 정수,\n'
            '  "comment": "20자 이내 찰진 한 줄 평"\n'
            "}"
        ),
    ])

    result = extract_json(response.text)
    today = datetime.now().strftime("%Y-%m-%d")

    with get_db() as conn:
        conn.execute(
            "INSERT INTO scores (user_id, room_id, date, godsaeng, dopamine, activity, comment) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, room_id, today,
             result["godsaeng"], result["dopamine"],
             result["activity"], result["comment"]),
        )
        conn.commit()

    return (
        f"## 분석 완료 — {user_id}\n\n"
        f"- **활동**: {result['activity']}\n"
        f"- **갓생 지수**: {result['godsaeng']}점\n"
        f"- **도파민 지수**: {result['dopamine']}점\n"
        f"- **한 줄 평**: {result['comment']}"
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Query Room Scores",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def query_scores(room_id: str, date: str = "") -> str:
    """Retrieves the daily GodSaeng(갓생) and Dopamine(도파민) score leaderboard for all users in a room from GodSaeng Salon(갓생살롱). Returns a ranked list with MVP and laziest member highlighted.

    Args:
        room_id: Group chat room ID
        date:    Date to query in YYYY-MM-DD format. Defaults to today if empty.
    """
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    scores = _fetch_scores(room_id, date)

    if not scores:
        return f"## {date} 점수 현황\n\n아직 인증한 멤버가 없습니다."

    mvp = scores[0]["user_id"]
    laziest = max(scores, key=lambda x: x["dopamine"])["user_id"]

    lines = [f"## {date} 갓생살롱 점수 현황\n"]
    for i, s in enumerate(scores, 1):
        lines.append(
            f"{i}. **{s['user_id']}** — 갓생 {s['godsaeng']}점 / 도파민 {s['dopamine']}점 ({s['activities']})"
        )
    lines.append(f"\n**MVP**: {mvp}  |  **베짱이**: {laziest}")

    return "\n".join(lines)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Generate Daily Award Briefing",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
)
def generate_briefing(room_id: str, date: str = "") -> str:
    """Generates a witty daily award briefing using GodSaeng Salon(갓생살롱) based on the day's score data. Awards the MVP for highest GodSaeng(갓생) score and the laziest member for highest Dopamine(도파민) score with humorous commentary.

    Args:
        room_id: Group chat room ID
        date:    Briefing date in YYYY-MM-DD format. Defaults to today if empty.
    """
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    scores = _fetch_scores(room_id, date)

    if not scores:
        return "오늘 인증 데이터가 없어요. 내일은 꼭 올려주세요. 기억하고 있을게요."

    mvp = scores[0]["user_id"]
    laziest = max(scores, key=lambda x: x["dopamine"])["user_id"]

    model = get_model()
    response = model.generate_content(
        f"다음 데이터로 갓생살롱 데일리 어워드 브리핑을 작성하세요.\n\n"
        f"날짜: {date}\n"
        f"MVP(갓생 1위): {mvp}\n"
        f"베짱이(도파민 1위): {laziest}\n"
        f"점수 데이터: {json.dumps(scores, ensure_ascii=False)}\n\n"
        "조건:\n"
        "- 갓생 점수 1위 → MVP 어워드\n"
        "- 도파민 점수 1위 → 베짱이 어워드\n"
        "- 각 수상자에게 칭찬인 척 깎는 or 공감인 척 찌르는 멘트\n"
        "- 방 전체 총평 한 줄\n"
        "- 이모지 적당히, 전체 200자 이내"
    )

    return response.text


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
