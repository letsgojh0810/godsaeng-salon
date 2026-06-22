import os
import re
import json
import base64
import sqlite3
from datetime import datetime

import httpx
from google import genai
from google.genai import types
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

load_dotenv()

port = int(os.getenv("PORT", "8000"))

mcp = FastMCP("godsaeng-salon", host="0.0.0.0", port=port)

MODEL = "gemini-2.5-flash"


def get_client():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY is not configured.")
    return genai.Client(api_key=api_key)


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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS goals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT    NOT NULL,
                room_id     TEXT    NOT NULL,
                date        TEXT    NOT NULL,
                goal        TEXT    NOT NULL,
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


def _fetch_goals(user_id: str, date: str) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT goal FROM goals WHERE user_id = ? AND date = ? ORDER BY id",
            (user_id, date),
        ).fetchall()
    return [r["goal"] for r in rows]


@mcp.tool(
    annotations=ToolAnnotations(
        title="Set Daily Goal",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def set_goal(user_id: str, goal: str, room_id: str = "default") -> str:
    """Sets a daily productivity goal for a user in GodSaeng Salon(갓생살롱). Stores the goal and returns an encouraging message to motivate the user.

    Args:
        user_id: User ID (e.g., minjun)
        goal:    Goal description (e.g., 헬스 1시간, 독서 30분, 카공 2시간)
        room_id: Room ID, defaults to "default"
    """
    date = datetime.now().strftime("%Y-%m-%d")

    with get_db() as conn:
        conn.execute(
            "INSERT INTO goals (user_id, room_id, date, goal) VALUES (?, ?, ?, ?)",
            (user_id, room_id, date, goal),
        )
        conn.commit()

    return (
        f"## 목표 설정 완료!\n\n"
        f"- **유저**: {user_id}\n"
        f"- **오늘의 목표**: {goal}\n"
        f"- **날짜**: {date}\n\n"
        f"기억해뒀어. 나중에 인증샷 보내줘! 믿고 있을게 💪"
    )


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
    """Analyzes a daily lifestyle photo using GodSaeng Salon(갓생살롱) and measures the GodSaeng(갓생) productivity index and Dopamine(도파민) index. Checks against the user's set goal and stores the score in the database.

    Args:
        image_url: URL of the photo to analyze
        user_id:   User ID who uploaded the photo (e.g., minjun)
        room_id:   Room ID (e.g., room_abc123)
    """
    try:
        resp = httpx.get(image_url, follow_redirects=True, timeout=30)
        resp.raise_for_status()

        media_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]
        image_bytes = resp.content

        today = datetime.now().strftime("%Y-%m-%d")
        goals = _fetch_goals(user_id, today)
        goal_context = f"\n참고로 이 유저의 오늘 목표는 '{', '.join(goals)}'입니다. 목표와 관련된 활동이면 갓생 점수를 더 높게 주세요." if goals else ""

        client = get_client()
        response = client.models.generate_content(
            model=MODEL,
            contents=[
                types.Part(inline_data=types.Blob(mime_type=media_type, data=image_bytes)),
                types.Part(text=(
                    "이 사진을 분석하여 아래 JSON 형식으로만 응답하세요. "
                    "다른 텍스트는 절대 포함하지 마세요.\n\n"
                    "{\n"
                    '  "activity": "카공/헬스/독서/업무/침대눕방/게임/배달음식/기타 중 하나",\n'
                    '  "godsaeng": 0~30 사이 정수,\n'
                    '  "dopamine": 0~30 사이 정수,\n'
                    '  "comment": "20자 이내 찰진 한 줄 평"\n'
                    "}"
                    + goal_context
                )),
            ],
        )

        result = extract_json(response.text)

        with get_db() as conn:
            conn.execute(
                "INSERT INTO scores (user_id, room_id, date, godsaeng, dopamine, activity, comment) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, room_id, today,
                 result["godsaeng"], result["dopamine"],
                 result["activity"], result["comment"]),
            )
            conn.commit()

        goal_msg = f"\n- **오늘 목표**: {', '.join(goals)}" if goals else ""

        return (
            f"## 분석 완료 — {user_id}\n\n"
            f"- **활동**: {result['activity']}{goal_msg}\n"
            f"- **갓생 지수**: {result['godsaeng']}점\n"
            f"- **도파민 지수**: {result['dopamine']}점\n"
            f"- **한 줄 평**: {result['comment']}"
        )
    except Exception as e:
        return f"[ERROR] {type(e).__name__}: {e}"


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
        room_id: Room ID
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
    """Generates a witty daily award briefing using GodSaeng Salon(갓생살롱) based on the day's score and goal data. Awards MVP and laziest member with humorous commentary.

    Args:
        room_id: Room ID
        date:    Briefing date in YYYY-MM-DD format. Defaults to today if empty.
    """
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    scores = _fetch_scores(room_id, date)

    if not scores:
        return "오늘 인증 데이터가 없어요. 내일은 꼭 올려주세요. 기억하고 있을게요."

    mvp = scores[0]["user_id"]
    laziest = max(scores, key=lambda x: x["dopamine"])["user_id"]

    goals_by_user = {s["user_id"]: _fetch_goals(s["user_id"], date) for s in scores}

    client = get_client()
    response = client.models.generate_content(
        model=MODEL,
        contents=types.Part(text=(
            f"다음 데이터로 갓생살롱 데일리 어워드 브리핑을 작성하세요.\n\n"
            f"날짜: {date}\n"
            f"MVP(갓생 1위): {mvp}\n"
            f"베짱이(도파민 1위): {laziest}\n"
            f"점수 데이터: {json.dumps(scores, ensure_ascii=False)}\n"
            f"오늘의 목표: {json.dumps(goals_by_user, ensure_ascii=False)}\n\n"
            "조건:\n"
            "- 갓생 점수 1위 → MVP 어워드\n"
            "- 도파민 점수 1위 → 베짱이 어워드\n"
            "- 목표를 달성한 사람은 칭찬, 못 한 사람은 공감인 척 찌르는 멘트\n"
            "- 방 전체 총평 한 줄\n"
            "- 이모지 적당히, 전체 200자 이내"
        )),
    )

    return response.text


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
