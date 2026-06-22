import os
import json
import base64
import sqlite3
from datetime import datetime

import httpx
import anthropic
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

port = int(os.getenv("PORT", "8000"))

mcp = FastMCP("godsaeng-salon", host="0.0.0.0", port=port)
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

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


@mcp.tool()
def analyze_photo(image_url: str, user_id: str, room_id: str) -> dict:
    """
    일상 사진을 분석해 갓생 지수와 도파민 지수를 측정하고 DB에 저장합니다.

    Args:
        image_url: 분석할 사진의 URL
        user_id:   사진을 올린 유저 ID (예: minjun)
        room_id:   단톡방 ID (예: room_abc123)
    """
    resp = httpx.get(image_url, follow_redirects=True, timeout=30)
    resp.raise_for_status()

    media_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]
    image_b64 = base64.standard_b64encode(resp.content).decode("utf-8")

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_b64,
                    },
                },
                {
                    "type": "text",
                    "text": (
                        "이 사진을 분석하여 아래 JSON 형식으로만 응답하세요. "
                        "다른 텍스트는 절대 포함하지 마세요.\n\n"
                        "{\n"
                        '  "activity": "카공/헬스/독서/업무/침대눕방/게임/배달음식/기타 중 하나",\n'
                        '  "godsaeng": 0~30 사이 정수,\n'
                        '  "dopamine": 0~30 사이 정수,\n'
                        '  "comment": "20자 이내 찰진 한 줄 평"\n'
                        "}"
                    ),
                },
            ],
        }],
    )

    result = json.loads(message.content[0].text.strip())
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

    return result


@mcp.tool()
def query_scores(room_id: str, date: str = "") -> dict:
    """
    방의 갓생/도파민 점수를 조회합니다.

    Args:
        room_id: 단톡방 ID
        date:    조회 날짜 (YYYY-MM-DD). 비워두면 오늘.
    """
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

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

    scores = [dict(r) for r in rows]

    return {
        "date": date,
        "room_id": room_id,
        "scores": scores,
        "mvp": scores[0]["user_id"] if scores else None,
        "laziest": max(scores, key=lambda x: x["dopamine"])["user_id"] if scores else None,
    }


@mcp.tool()
def generate_briefing(room_id: str, date: str = "") -> str:
    """
    하루 점수 데이터를 종합하여 데일리 어워드 브리핑 텍스트를 생성합니다.

    Args:
        room_id: 단톡방 ID
        date:    브리핑 날짜 (YYYY-MM-DD). 비워두면 오늘.
    """
    data = query_scores(room_id, date)

    if not data["scores"]:
        return "오늘 인증 데이터가 없어요. 내일은 꼭 올려주세요. 기억하고 있을게요. 🤖"

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": (
                f"다음 데이터로 갓생살롱 데일리 어워드 브리핑을 작성하세요.\n\n"
                f"날짜: {data['date']}\n"
                f"점수 데이터: {json.dumps(data['scores'], ensure_ascii=False)}\n\n"
                "조건:\n"
                "- 갓생 점수 1위 → MVP 어워드\n"
                "- 도파민 점수 1위 → 베짱이 어워드\n"
                "- 각 수상자에게 칭찬인 척 깎는 or 공감인 척 찌르는 멘트\n"
                "- 방 전체 총평 한 줄\n"
                "- 이모지 적당히, 전체 200자 이내"
            ),
        }],
    )

    return message.content[0].text


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
