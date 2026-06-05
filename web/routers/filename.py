"""
檔名解析 API 路由

端點：
- POST /api/parse-filename  — 批次解析檔名，提取番號與字幕標記
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Optional, List

from core.filename_identity import parse_media_identity

router = APIRouter(prefix="/api", tags=["filename"])


# ============ Pydantic 模型 ============

class ParseFilenameRequest(BaseModel):
    """批次檔名解析請求"""
    filenames: List[str] = Field(..., description="檔案名稱列表")


class ParsedFile(BaseModel):
    """單個檔案解析結果"""
    filename: str
    number: Optional[str] = None
    canonical_number: Optional[str] = None
    search_number: Optional[str] = None
    display_number: Optional[str] = None
    work_key: Optional[str] = None
    number_aliases: List[str] = Field(default_factory=list)
    source_queries: dict[str, List[str]] = Field(default_factory=dict)
    part_index: Optional[str] = None
    variant_flags: dict[str, bool] = Field(default_factory=dict)
    variant_label: str = ""
    raw_tokens: List[str] = Field(default_factory=list)
    has_subtitle: bool = False


class ParseFilenameResponse(BaseModel):
    """批次檔名解析響應"""
    results: List[ParsedFile]
    total: int
    parsed: int


# ============ API 端點 ============

@router.post("/parse-filename", response_model=ParseFilenameResponse)
async def parse_filename(request: ParseFilenameRequest) -> ParseFilenameResponse:
    """
    批次解析檔名，提取番號和字幕資訊

    Args:
        request: 包含檔名列表的請求

    Returns:
        ParseFilenameResponse: 解析結果

    Example:
        POST /api/parse-filename
        {"filenames": ["SONE-205.mp4", "[中文字幕] ABC-123.mkv"]}

        Response:
        {
            "results": [
                {"filename": "SONE-205.mp4", "number": "SONE-205", "has_subtitle": false},
                {"filename": "[中文字幕] ABC-123.mkv", "number": "ABC-123", "has_subtitle": true}
            ],
            "total": 2,
            "parsed": 2
        }
    """
    results = []
    parsed_count = 0

    for filename in request.filenames:
        identity = parse_media_identity(filename)
        number = identity.canonical_number
        variant_flags = identity.to_dict()["variant_flags"]
        has_subtitle = bool(variant_flags.get("subtitle_cn"))

        results.append(ParsedFile(
            filename=filename,
            number=number,
            canonical_number=identity.canonical_number,
            search_number=identity.search_number,
            display_number=identity.display_number,
            work_key=identity.work_key,
            number_aliases=identity.number_aliases,
            source_queries=identity.source_queries,
            part_index=identity.part_index,
            variant_flags=variant_flags,
            variant_label=identity.variant_label,
            raw_tokens=identity.raw_tokens,
            has_subtitle=has_subtitle
        ))

        if number:
            parsed_count += 1

    return ParseFilenameResponse(
        results=results,
        total=len(request.filenames),
        parsed=parsed_count
    )
