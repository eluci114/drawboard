"""
캔버스 드로잉 명령 타입 정의.
AI나 클라이언트가 이 JSON 형식으로 그리기 요청을 보냅니다.
"""
from typing import Literal
from pydantic import BaseModel, Field


class Point(BaseModel):
    x: float
    y: float


# 드로잉 명령 유니온
class DrawLine(BaseModel):
    type: Literal["line"] = "line"
    x1: float
    y1: float
    x2: float
    y2: float
    color: str = "#000000"
    width: float = 2


class DrawCircle(BaseModel):
    type: Literal["circle"] = "circle"
    x: float
    y: float
    r: float
    color: str = "#000000"
    fill: bool = False
    width: float = 2


class DrawRect(BaseModel):
    type: Literal["rect"] = "rect"
    x: float
    y: float
    w: float
    h: float
    color: str = "#000000"
    fill: bool = False
    width: float = 2


class DrawPath(BaseModel):
    type: Literal["path"] = "path"
    points: list[Point]
    color: str = "#000000"
    width: float = 2
    close: bool = False


class DrawClear(BaseModel):
    type: Literal["clear"] = "clear"


DrawAction = DrawLine | DrawCircle | DrawRect | DrawPath | DrawClear


class DrawEvent(BaseModel):
    """캔버스에 적용할 단일 드로잉 이벤트 (AI 이름 포함)."""
    ai_name: str = "Anonymous"
    action: DrawLine | DrawCircle | DrawRect | DrawPath | DrawClear

    def to_broadcast(self) -> dict:
        return {
            "ai_name": self.ai_name,
            "action": self.action.model_dump(),
        }
