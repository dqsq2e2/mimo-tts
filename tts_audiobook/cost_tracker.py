"""成本追踪：token 估算、用量记录、预算控制、最终报告。"""

import time
from dataclasses import dataclass, field

from .config import TOKEN_ESTIMATE_PER_CHAR, PRICING_PER_1M


@dataclass
class ChunkRecord:
    """单块合成记录。"""

    index: int
    chars: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_input: float = 0.0         # 若付费的输入费用
    cost_output: float = 0.0        # 若付费的输出费用
    duration_sec: float = 0.0       # 生成音频时长
    elapsed_sec: float = 0.0        # API 调用耗时


@dataclass
class CostTracker:
    """成本追踪器。"""

    max_cost_yuan: float = float("inf")
    is_free: bool = True            # 当前 TTS 限时免费
    records: list[ChunkRecord] = field(default_factory=list)
    _start_time: float = field(default_factory=time.time)

    def estimate(self, chunks: list[str]) -> dict:
        """预估分块后的费用。"""
        total_chars = sum(len(c) for c in chunks)
        est_tokens = int(total_chars * TOKEN_ESTIMATE_PER_CHAR)
        est_input_cost = est_tokens * PRICING_PER_1M["input"] / 1_000_000
        est_output_cost = est_tokens * PRICING_PER_1M["output"] / 1_000_000

        return {
            "chunks": len(chunks),
            "total_chars": total_chars,
            "est_tokens": est_tokens,
            "est_input_cost": est_input_cost,
            "est_output_cost": est_output_cost,
            "est_total_cost": est_input_cost + est_output_cost,
            "is_free": self.is_free,
            "actual_cost": 0.0 if self.is_free else est_input_cost + est_output_cost,
        }

    def record(self, index: int, chars: int, usage: dict, audio_duration: float, elapsed: float):
        """记录一次合成。"""
        it = PRICING_PER_1M["input"] / 1_000_000
        ot = PRICING_PER_1M["output"] / 1_000_000
        rec = ChunkRecord(
            index=index,
            chars=chars,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            cost_input=usage.get("prompt_tokens", 0) * it,
            cost_output=usage.get("completion_tokens", 0) * ot,
            duration_sec=audio_duration,
            elapsed_sec=elapsed,
        )
        self.records.append(rec)

    def would_exceed_budget(self, extra_cost: float = 0) -> bool:
        """检查是否即将超出预算。"""
        if self.is_free or self.max_cost_yuan == float("inf"):
            return False
        total = self.total_would_be_cost + extra_cost
        return total >= self.max_cost_yuan

    @property
    def total_would_be_cost(self) -> float:
        """若无免费的累计费用。"""
        return sum(r.cost_input + r.cost_output for r in self.records)

    @property
    def total_actual_cost(self) -> float:
        """实际费用（免费期间为 0）。"""
        return 0.0 if self.is_free else self.total_would_be_cost

    @property
    def total_tokens(self) -> int:
        return sum(r.prompt_tokens + r.completion_tokens for r in self.records)

    @property
    def total_duration_sec(self) -> float:
        return sum(r.duration_sec for r in self.records)

    @property
    def elapsed_total_sec(self) -> float:
        return time.time() - self._start_time

    def progress_summary(self, current: int, total: int) -> str:
        """进度摘要（每块完成后调用）。"""
        elapsed = self.elapsed_total_sec
        if current > 0:
            eta = (elapsed / current) * (total - current)
        else:
            eta = 0
        return (
            f"块 {current}/{total} | "
            f"累计 tokens: {self.total_tokens:,} | "
            f"费用: ¥{self.total_actual_cost:.4f}"
            f"{' (免费)' if self.is_free else ''} | "
            f"耗时: {elapsed:.0f}s | "
            f"预计剩余: {eta:.0f}s"
        )

    def final_report(self) -> str:
        """生成最终费用报告。"""
        elapsed = self.elapsed_total_sec
        lines = [
            "=" * 55,
            "  合成完成 - 费用报告",
            "=" * 55,
            f"  总块数:         {len(self.records)}",
            f"  总 Token:       {self.total_tokens:,}",
            f"  总音频时长:     {self.total_duration_sec/60:.1f} 分钟",
            f"  总耗时:         {elapsed:.0f}s",
            f"  API 调用次数:   {len(self.records)}",
        ]
        if self.is_free:
            lines.append(
                f"  若无免费应计:   ¥{self.total_would_be_cost:.4f} "
                f"(输入 ¥{sum(r.cost_input for r in self.records):.4f} + "
                f"输出 ¥{sum(r.cost_output for r in self.records):.4f})"
            )
        lines.append(f"  实际费用:       ¥{self.total_actual_cost:.4f}")
        lines.append("=" * 55)
        return "\n".join(lines)
