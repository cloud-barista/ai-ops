from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from textwrap import wrap
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "docs" / "images"


COLORS = {
    "bg": "#f8fbff",
    "surface": "#ffffff",
    "surface_blue": "#edf6ff",
    "surface_green": "#edf9f1",
    "surface_amber": "#fff7e8",
    "surface_red": "#fff1f2",
    "navy": "#123a63",
    "blue": "#2267b8",
    "blue_dark": "#174d91",
    "slate": "#41576f",
    "muted": "#728399",
    "line": "#b8c7d8",
    "green": "#16834a",
    "amber": "#c97800",
    "red": "#d93b4a",
}


FONT_REGULAR = Path(r"C:\Windows\Fonts\malgun.ttf")
FONT_BOLD = Path(r"C:\Windows\Fonts\malgunbd.ttf")
if not FONT_REGULAR.exists():
    FONT_REGULAR = Path(r"C:\Windows\Fonts\arial.ttf")
if not FONT_BOLD.exists():
    FONT_BOLD = FONT_REGULAR


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_BOLD if bold else FONT_REGULAR), size)


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int
    title: str
    lines: tuple[str, ...] = ()
    fill: str = COLORS["surface"]
    outline: str = COLORS["blue"]
    badge: str | None = None
    title_size: int = 28
    body_size: int = 21


class Diagram:
    def __init__(self, width: int, height: int, title: str, subtitle: str = "") -> None:
        self.width = width
        self.height = height
        self.title = title
        self.subtitle = subtitle
        self.image = Image.new("RGB", (width, height), COLORS["bg"])
        self.draw = ImageDraw.Draw(self.image)
        self.svg: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            "<defs>",
            '<filter id="softShadow" x="-20%" y="-20%" width="140%" height="140%">',
            '<feDropShadow dx="0" dy="10" stdDeviation="10" flood-color="#1b3c5d" flood-opacity="0.12"/>',
            "</filter>",
            "</defs>",
            f'<rect width="{width}" height="{height}" fill="{COLORS["bg"]}"/>',
        ]
        self.header()

    def header(self) -> None:
        self.draw.text((58, 44), self.title, fill=COLORS["navy"], font=font(38, True))
        self.svg_text(58, 74, self.title, 38, COLORS["navy"], bold=True)
        if self.subtitle:
            self.draw.text((61, 94), self.subtitle, fill=COLORS["slate"], font=font(20))
            self.svg_text(61, 118, self.subtitle, 20, COLORS["slate"])
        self.draw.rounded_rectangle((self.width - 365, 48, self.width - 58, 92), radius=22, fill="#e9f2ff", outline="#c6d9f2", width=1)
        self.draw.text((self.width - 340, 59), "Kyung Hee OPS · Go Prototype", fill=COLORS["blue_dark"], font=font(17, True))
        self.svg_round_rect(self.width - 365, 48, 307, 44, 22, "#e9f2ff", "#c6d9f2", 1)
        self.svg_text(self.width - 340, 76, "Kyung Hee OPS · Go Prototype", 17, COLORS["blue_dark"], bold=True)

    def save(self, stem: str) -> None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        self.svg.append("</svg>")
        (OUT_DIR / f"{stem}.svg").write_text("\n".join(self.svg), encoding="utf-8")
        self.image.save(OUT_DIR / f"{stem}.png", quality=96)

    def panel(self, x: int, y: int, w: int, h: int, title: str, fill: str = "#ffffff") -> None:
        self.draw.rounded_rectangle((x + 6, y + 8, x + w + 6, y + h + 8), radius=26, fill="#d8e5f3")
        self.draw.rounded_rectangle((x, y, x + w, y + h), radius=26, fill=fill, outline="#d5e1ee", width=2)
        self.draw.text((x + 28, y + 24), title, fill=COLORS["navy"], font=font(24, True))
        self.svg_round_rect(x + 6, y + 8, w, h, 26, "#d8e5f3", "#d8e5f3", 0, opacity=0.55)
        self.svg_round_rect(x, y, w, h, 26, fill, "#d5e1ee", 2)
        self.svg_text(x + 28, y + 55, title, 24, COLORS["navy"], bold=True)

    def box(self, b: Box) -> None:
        x, y, w, h = b.x, b.y, b.w, b.h
        self.draw.rounded_rectangle((x + 5, y + 7, x + w + 5, y + h + 7), radius=20, fill="#dce7f4")
        self.draw.rounded_rectangle((x, y, x + w, y + h), radius=20, fill=b.fill, outline=b.outline, width=3)
        if b.badge:
            self.draw.ellipse((x + 18, y + 20, x + 54, y + 56), fill=b.outline)
            self.draw.text((x + 31, y + 25), b.badge, fill="#ffffff", font=font(18, True), anchor="mm")
        title_x = x + (68 if b.badge else 26)
        self.draw.text((title_x, y + 25), b.title, fill=COLORS["navy"], font=font(b.title_size, True))
        yy = y + 62
        for line in b.lines:
            self.draw.text((x + 26, yy), line, fill=COLORS["slate"], font=font(b.body_size))
            yy += b.body_size + 6

        self.svg_round_rect(x + 5, y + 7, w, h, 20, "#dce7f4", "#dce7f4", 0, opacity=0.65)
        self.svg_round_rect(x, y, w, h, 20, b.fill, b.outline, 3)
        if b.badge:
            self.svg.append(f'<circle cx="{x + 36}" cy="{y + 38}" r="18" fill="{b.outline}"/>')
            self.svg_text(x + 36, y + 45, b.badge, 18, "#ffffff", bold=True, anchor="middle")
        self.svg_text(title_x, y + 55, b.title, b.title_size, COLORS["navy"], bold=True)
        svg_y = y + 88
        for line in b.lines:
            self.svg_text(x + 26, svg_y, line, b.body_size, COLORS["slate"])
            svg_y += b.body_size + 6

    def chip(self, x: int, y: int, text: str, fill: str, outline: str, color: str | None = None) -> None:
        width = self.draw.textlength(text, font=font(18, True)) + 34
        self.draw.rounded_rectangle((x, y, x + width, y + 36), radius=18, fill=fill, outline=outline, width=2)
        self.draw.text((x + 17, y + 8), text, fill=color or outline, font=font(18, True))
        self.svg_round_rect(x, y, int(width), 36, 18, fill, outline, 2)
        self.svg_text(x + 17, y + 31, text, 18, color or outline, bold=True)

    def small_text(self, x: int, y: int, text: str, size: int = 18, color: str = COLORS["muted"], bold: bool = False) -> None:
        self.draw.text((x, y), text, fill=color, font=font(size, bold))
        self.svg_text(x, y + size + 4, text, size, color, bold=bold)

    def arrow(self, x1: int, y1: int, x2: int, y2: int, color: str = COLORS["blue_dark"], width: int = 4) -> None:
        self.draw.line((x1, y1, x2, y2), fill=color, width=width)
        angle = math.atan2(y2 - y1, x2 - x1)
        size = 14
        points = [
            (x2, y2),
            (x2 - size * math.cos(angle - math.pi / 6), y2 - size * math.sin(angle - math.pi / 6)),
            (x2 - size * math.cos(angle + math.pi / 6), y2 - size * math.sin(angle + math.pi / 6)),
        ]
        self.draw.polygon(points, fill=color)
        self.svg.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{width}" stroke-linecap="round"/>'
        )
        p = " ".join(f"{int(px)},{int(py)}" for px, py in points)
        self.svg.append(f'<polygon points="{p}" fill="{color}"/>')

    def elbow_arrow(self, points: list[tuple[int, int]], color: str = COLORS["blue_dark"], width: int = 4) -> None:
        for a, b in zip(points, points[1:]):
            self.draw.line((*a, *b), fill=color, width=width)
            self.svg.append(
                f'<line x1="{a[0]}" y1="{a[1]}" x2="{b[0]}" y2="{b[1]}" stroke="{color}" stroke-width="{width}" stroke-linecap="round"/>'
            )
        self.arrow_head(points[-2], points[-1], color)

    def arrow_head(self, p1: tuple[int, int], p2: tuple[int, int], color: str) -> None:
        x1, y1 = p1
        x2, y2 = p2
        angle = math.atan2(y2 - y1, x2 - x1)
        size = 14
        points = [
            (x2, y2),
            (x2 - size * math.cos(angle - math.pi / 6), y2 - size * math.sin(angle - math.pi / 6)),
            (x2 - size * math.cos(angle + math.pi / 6), y2 - size * math.sin(angle + math.pi / 6)),
        ]
        self.draw.polygon(points, fill=color)
        p = " ".join(f"{int(px)},{int(py)}" for px, py in points)
        self.svg.append(f'<polygon points="{p}" fill="{color}"/>')

    def bullet_list(self, x: int, y: int, items: list[str], color: str = COLORS["slate"]) -> None:
        yy = y
        for item in items:
            self.draw.ellipse((x, yy + 8, x + 8, yy + 16), fill=COLORS["blue"])
            self.draw.text((x + 20, yy), item, fill=color, font=font(20))
            self.svg.append(f'<circle cx="{x + 4}" cy="{yy + 12}" r="4" fill="{COLORS["blue"]}"/>')
            self.svg_text(x + 20, yy + 24, item, 20, color)
            yy += 38

    def svg_round_rect(self, x: int, y: int, w: int, h: int, r: int, fill: str, stroke: str, sw: int, opacity: float = 1.0) -> None:
        opacity_attr = f' opacity="{opacity}"' if opacity < 1.0 else ""
        self.svg.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{opacity_attr}/>'
        )

    def svg_text(
        self,
        x: int,
        y: int,
        text: str,
        size: int,
        color: str,
        bold: bool = False,
        anchor: str = "start",
    ) -> None:
        weight = "700" if bold else "400"
        self.svg.append(
            f'<text x="{x}" y="{y}" fill="{color}" font-family="Malgun Gothic, Pretendard, Noto Sans KR, Arial, sans-serif" '
            f'font-size="{size}" font-weight="{weight}" text-anchor="{anchor}">{escape(text)}</text>'
        )


def architecture() -> None:
    d = Diagram(
        1800,
        1040,
        "AI 기반 서비스 제어 및 관리 자동화 프레임워크",
        "Go 기반 LLM 운영 관리, Agent Registry, CPU/GPU VM 배치 판단, 배포·제어 검증 구조",
    )
    d.panel(58, 170, 360, 720, "운영 입력")
    input_boxes = [
        Box(92, 250, 292, 120, "Ops 시나리오", ("장애·지연·비용 상황", "운영 정책과 metric"), COLORS["surface_blue"], COLORS["blue"], "1", 24, 19),
        Box(92, 400, 292, 120, "후보 LLM", ("role label + 실제 모델", "benchmark status"), COLORS["surface"], COLORS["blue"], "2", 24, 19),
        Box(92, 550, 292, 120, "Agent Registry", ("역할·권한·bounded action", "reward signal"), COLORS["surface"], COLORS["blue"], "3", 24, 19),
        Box(92, 700, 292, 120, "VM/Workload", ("CPU/GPU 요구사항", "SLO·capacity·cost"), COLORS["surface"], COLORS["blue"], "4", 24, 19),
    ]
    for b in input_boxes:
        d.box(b)

    d.panel(460, 170, 820, 720, "Go Service-Control Core")
    core_boxes = [
        Box(505, 280, 320, 140, "LLM 운영 관리", ("후보 평가·선정 정책", "dry-run / executed 구분"), COLORS["surface_blue"], COLORS["blue"], None, 25, 19),
        Box(915, 280, 320, 140, "에이전트 등록 관리", ("등록·조회·상세 확인", "허용 action 검증"), COLORS["surface_blue"], COLORS["blue"], None, 25, 19),
        Box(505, 515, 320, 140, "추론 배치 판단", ("VRAM/SLO/throughput", "resource scoring"), COLORS["surface_blue"], COLORS["blue"], None, 25, 19),
        Box(915, 515, 320, 140, "배포·제어 계획", ("scale / monitor / rollback", "운영 readiness 출력"), COLORS["surface_blue"], COLORS["blue"], None, 25, 19),
    ]
    for b in core_boxes:
        d.box(b)
    d.arrow(825, 350, 915, 350)
    d.elbow_arrow([(1075, 420), (1075, 465), (665, 465), (665, 515)])
    d.arrow(825, 585, 915, 585)
    d.box(Box(610, 735, 520, 95, "Go Guard", ("허용 action 검증, 위험한 실행 경계 확인",), COLORS["surface_amber"], COLORS["amber"], None, 25, 20))
    d.elbow_arrow([(1075, 655), (1075, 720), (870, 720), (870, 735)], COLORS["amber"])
    d.chip(525, 235, "Echo API / CLI", "#e9f2ff", COLORS["blue"])
    d.chip(705, 235, "JSON config", "#eef7f0", COLORS["green"])
    d.chip(900, 235, "OpenAPI", "#fff3df", COLORS["amber"])

    d.panel(1320, 170, 420, 720, "산출물과 검증 증적")
    output_boxes = [
        Box(1355, 250, 350, 120, "구조 설계서", ("LLM 운영 관리 구조", "선정 기준과 평가 흐름"), COLORS["surface"], COLORS["blue"], None, 24, 19),
        Box(1355, 400, 350, 120, "등록 관리 프로토타입", ("Agent metadata", "bounded action API"), COLORS["surface"], COLORS["blue"], None, 24, 19),
        Box(1355, 550, 350, 120, "추론 최적화 전략", ("CPU/GPU VM 배치", "배포·제어 계획"), COLORS["surface"], COLORS["blue"], None, 24, 19),
        Box(1355, 700, 350, 120, "검증 증적", ("local / VM validation", "JSON summary / logs"), COLORS["surface_green"], COLORS["green"], None, 24, 19),
    ]
    for b in output_boxes:
        d.box(b)
    d.arrow(1280, 350, 1320, 350)
    d.arrow(1280, 585, 1320, 585)
    d.arrow(1280, 782, 1320, 782, COLORS["green"])
    d.small_text(
        74,
        954,
        "핵심 경계: 기본 실험은 Go prototype 검증이며, 실제 LLM endpoint와 GPU VM 검증은 benchmark_status와 target 값으로 분리 기록한다.",
        20,
        COLORS["slate"],
    )
    d.save("service_control_architecture")


def llm_operation_flow() -> None:
    d = Diagram(
        1700,
        900,
        "LLM 운영 관리 구조",
        "Ops 분석 시나리오 기반 후보 LLM 평가, 실행 상태 구분, 최종 선정 결과 연결",
    )
    d.panel(70, 170, 420, 560, "입력")
    d.box(Box(110, 255, 340, 125, "Ops Scenario Set", ("장애·지연·비용 문제", "expected action 포함"), COLORS["surface"], COLORS["blue"], "1", 24, 19))
    d.box(Box(110, 430, 340, 125, "Candidate Config", ("provider / endpoint", "actual_model / role label"), COLORS["surface"], COLORS["blue"], "2", 24, 19))
    d.chip(118, 620, "JSONL scenarios", "#e9f2ff", COLORS["blue"])
    d.chip(118, 672, "candidate ranking", "#eef7f0", COLORS["green"])

    d.panel(565, 170, 420, 560, "실행")
    d.box(Box(610, 285, 330, 150, "Go Benchmark Runner", ("동일 prompt 입력", "candidate별 응답 저장"), COLORS["surface_blue"], COLORS["blue"], None, 25, 20))
    d.box(Box(610, 500, 330, 120, "Model Output Store", ("model_outputs.jsonl", "latency / error 기록"), COLORS["surface"], COLORS["blue"], None, 24, 19))
    d.arrow(775, 435, 775, 500)

    d.panel(1060, 170, 570, 560, "평가와 선정")
    d.box(Box(1105, 255, 220, 130, "JSON 검사", ("형식·필드", "파싱 가능 여부"), COLORS["surface"], COLORS["blue"], None, 23, 18))
    d.box(Box(1365, 255, 220, 130, "Action 검사", ("allowed action", "expected match"), COLORS["surface"], COLORS["blue"], None, 23, 18))
    d.box(Box(1105, 455, 220, 130, "운영 점수", ("품질·지연·비용", "정책 가중치"), COLORS["surface"], COLORS["blue"], None, 23, 18))
    d.box(Box(1365, 455, 220, 130, "선정 결과", ("selected_actual_model", "benchmark_status"), COLORS["surface_green"], COLORS["green"], None, 23, 18))
    d.arrow(985, 355, 1060, 355)
    d.arrow(1325, 320, 1365, 320)
    d.elbow_arrow([(1215, 385), (1215, 420), (1215, 455)])
    d.arrow(1325, 520, 1365, 520, COLORS["green"])

    d.small_text(90, 780, "상태값 해석", 22, COLORS["navy"], True)
    d.chip(235, 770, "not_executed: 정책 baseline", "#fff7e8", COLORS["amber"])
    d.chip(565, 770, "dry_run: 연결 구조 검증", "#fff7e8", COLORS["amber"])
    d.chip(890, 770, "executed: 실제 endpoint 응답 평가", "#edf9f1", COLORS["green"])
    d.save("llm_operation_flow")


def agent_registry_flow() -> None:
    d = Diagram(
        1600,
        820,
        "에이전트 등록 관리 프로토타입",
        "AI 에이전트 metadata, 권한, bounded action을 Go API와 검증 로직으로 관리",
    )
    d.panel(70, 170, 360, 500, "Registry Source")
    d.box(Box(105, 260, 290, 125, "Agent JSON", ("name / role / enabled", "responsibility"), COLORS["surface"], COLORS["blue"], "1", 24, 19))
    d.box(Box(105, 445, 290, 125, "Action Policy", ("allowed action set", "reward signal"), COLORS["surface"], COLORS["blue"], "2", 24, 19))

    d.panel(500, 170, 500, 500, "Go Registry API")
    d.box(Box(545, 250, 185, 130, "List", ("등록 목록", "enabled 상태"), COLORS["surface_blue"], COLORS["blue"], None, 24, 18))
    d.box(Box(770, 250, 185, 130, "Detail", ("역할·책임", "입출력 조건"), COLORS["surface_blue"], COLORS["blue"], None, 24, 18))
    d.box(Box(545, 450, 410, 130, "Validate Action", ("요청 action이 agent 권한 안에 있는지 확인", "불허 action은 실행 전 차단"), COLORS["surface_amber"], COLORS["amber"], None, 24, 18))

    d.panel(1070, 170, 455, 500, "운영 결과")
    d.box(Box(1115, 250, 345, 120, "Valid Readiness", ("허용 action이면 readiness 반영", "service operations report 연결"), COLORS["surface_green"], COLORS["green"], None, 24, 19))
    d.box(Box(1115, 450, 345, 120, "Rejected Action", ("허용 범위 밖이면 거부", "위험 실행 방지"), COLORS["surface_red"], COLORS["red"], None, 24, 19))

    d.arrow(430, 322, 545, 322)
    d.arrow(430, 507, 545, 507, COLORS["amber"])
    d.arrow(730, 315, 770, 315)
    d.elbow_arrow([(955, 515), (1035, 515), (1035, 310), (1115, 310)], COLORS["green"])
    d.elbow_arrow([(955, 515), (1035, 515), (1035, 510), (1115, 510)], COLORS["red"])
    d.small_text(
        95,
        735,
        "핵심: agent가 할 수 있는 작업을 명시적으로 제한하고, 실제 배포·제어 요청은 bounded action 검증을 먼저 통과해야 한다.",
        20,
        COLORS["slate"],
    )
    d.save("agent_registry_flow")


def cpu_gpu_placement_flow() -> None:
    d = Diagram(
        1800,
        920,
        "CPU/GPU VM 기반 AI 응용 배포·제어 추론 최적화 전략",
        "Workload 요구사항을 기준으로 resource 후보를 필터링하고, SLO·capacity·cost 점수로 배치 판단",
    )
    d.panel(65, 170, 360, 570, "요구사항 입력")
    d.box(Box(100, 260, 290, 130, "Workload Profile", ("model type / SLO", "VRAM / throughput"), COLORS["surface"], COLORS["blue"], "1", 24, 19))
    d.box(Box(100, 455, 290, 130, "Control Policy", ("scale / monitor / rollback", "cost guardrail"), COLORS["surface"], COLORS["blue"], "2", 24, 19))

    d.panel(485, 170, 690, 570, "배치 판단")
    d.box(Box(525, 250, 275, 125, "Candidate Filtering", ("지원 모델 확인", "제약 후보 제외"), COLORS["surface_blue"], COLORS["blue"], None, 23, 18))
    d.box(Box(860, 250, 275, 125, "GPU 조건 확인", ("accelerator", "VRAM capacity"), COLORS["surface_blue"], COLORS["blue"], None, 23, 18))
    d.box(Box(525, 450, 275, 125, "SLO Check", ("latency", "throughput"), COLORS["surface_blue"], COLORS["blue"], None, 23, 18))
    d.box(Box(860, 450, 275, 125, "Weighted Scoring", ("quality / cost", "capacity ranking"), COLORS["surface_blue"], COLORS["blue"], None, 23, 18))
    d.arrow(800, 312, 860, 312)
    d.elbow_arrow([(997, 375), (997, 420), (662, 420), (662, 450)])
    d.arrow(800, 512, 860, 512)

    d.panel(1235, 170, 500, 570, "배포·제어 출력")
    d.box(Box(1275, 250, 390, 120, "Selected Resource", ("예: gpu-vm-l4 또는 cpu-vm", "선정 사유와 점수 포함"), COLORS["surface_green"], COLORS["green"], None, 24, 19))
    d.box(Box(1275, 430, 390, 120, "Deployment Control Plan", ("deploy / scale / monitor", "rollback 조건"), COLORS["surface_green"], COLORS["green"], None, 24, 19))
    d.box(Box(1275, 610, 390, 92, "Evidence", ("JSON output과 실행 로그로 증적화",), COLORS["surface"], COLORS["blue"], None, 24, 19))
    d.arrow(1175, 512, 1275, 310, COLORS["green"])
    d.arrow(1470, 370, 1470, 430, COLORS["green"])
    d.arrow(1470, 550, 1470, 610, COLORS["green"])

    d.small_text(90, 800, "판단 기준", 22, COLORS["navy"], True)
    d.chip(220, 790, "SLO", "#e9f2ff", COLORS["blue"])
    d.chip(315, 790, "VRAM", "#e9f2ff", COLORS["blue"])
    d.chip(430, 790, "throughput", "#e9f2ff", COLORS["blue"])
    d.chip(605, 790, "capacity", "#e9f2ff", COLORS["blue"])
    d.chip(745, 790, "cost", "#fff7e8", COLORS["amber"])
    d.chip(845, 790, "risk guardrail", "#fff1f2", COLORS["red"])
    d.save("cpu_gpu_placement_flow")


def local_vm_validation_flow() -> None:
    d = Diagram(
        1700,
        900,
        "Local/VM 공통 검증 구조",
        "같은 Go 검증 명령을 로컬과 GPU VM에서 실행하고, target별 증적을 분리 저장",
    )
    d.box(Box(80, 285, 310, 135, "validate-system", ("--target local 또는 vm", "--output-dir runs/..."), COLORS["surface_blue"], COLORS["blue"], None, 26, 20))

    d.panel(470, 170, 380, 550, "Target Evidence")
    d.box(Box(515, 250, 290, 120, "Local Target", ("Go / Git / OS evidence", "prototype 기능 검증"), COLORS["surface"], COLORS["blue"], None, 24, 19))
    d.box(Box(515, 470, 290, 120, "VM Target", ("GPU / metadata evidence", "GPU VM 실행 환경 검증"), COLORS["surface_amber"], COLORS["amber"], None, 24, 19))

    d.panel(925, 170, 360, 550, "Common Validation")
    d.box(Box(970, 250, 270, 120, "Go Tests", ("aiops-guard", "service-control-api"), COLORS["surface"], COLORS["blue"], None, 24, 19))
    d.box(Box(970, 470, 270, 120, "Team Validation", ("산출물 3개 흐름", "valid=true 확인"), COLORS["surface"], COLORS["blue"], None, 24, 19))

    d.panel(1360, 170, 275, 550, "Evidence Output")
    d.box(Box(1388, 250, 220, 120, "System Summary", ("00_system_validation", "target 기록"), COLORS["surface_green"], COLORS["green"], None, 23, 18))
    d.box(Box(1388, 470, 220, 120, "Optional LLM", ("not_executed", "dry_run / executed"), COLORS["surface"], COLORS["blue"], None, 23, 18))

    d.arrow(390, 352, 515, 310)
    d.arrow(390, 352, 515, 530, COLORS["amber"])
    d.arrow(805, 310, 970, 310)
    d.arrow(805, 530, 970, 530, COLORS["amber"])
    d.arrow(1105, 370, 1105, 470)
    d.arrow(1240, 530, 1388, 530, COLORS["green"])
    d.elbow_arrow([(1240, 310), (1320, 310), (1320, 310), (1388, 310)], COLORS["green"])
    d.small_text(
        90,
        785,
        "해석: 로컬 검증은 Go 로직과 산출물 흐름 검증, VM 검증은 동일 로직을 실제 GPU VM 환경에서 실행했다는 환경 증적을 추가한다.",
        20,
        COLORS["slate"],
    )
    d.save("local_vm_validation_flow")


def main() -> None:
    architecture()
    llm_operation_flow()
    agent_registry_flow()
    cpu_gpu_placement_flow()
    local_vm_validation_flow()


if __name__ == "__main__":
    main()
