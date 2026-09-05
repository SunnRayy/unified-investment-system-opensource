from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

class TrendStatus(str, Enum):
    STRONG_BULL = 'STRONG_BULL'
    BULL = 'BULL'
    NEUTRAL = 'NEUTRAL'
    BEAR = 'BEAR'
    STRONG_BEAR = 'STRONG_BEAR'

class MACDStatus(str, Enum):
    GOLDEN_CROSS = 'GOLDEN_CROSS'
    BULLISH = 'BULLISH'
    NEUTRAL = 'NEUTRAL'
    BEARISH = 'BEARISH'
    DEATH_CROSS = 'DEATH_CROSS'

class RSIStatus(str, Enum):
    OVERBOUGHT = 'OVERBOUGHT'
    NEUTRAL = 'NEUTRAL'
    OVERSOLD = 'OVERSOLD'

class VolumeStatus(str, Enum):
    SURGING = 'SURGING'
    HIGH = 'HIGH'
    NORMAL = 'NORMAL'
    LOW = 'LOW'

@dataclass
class TechnicalSignals:
    trend_status: TrendStatus
    ma5: Optional[float]
    ma10: Optional[float]
    ma20: Optional[float]
    ma_alignment_score: int          # 0=bearish, 1=partial, 2=full bull
    rsi_value: Optional[float]
    rsi_status: RSIStatus
    macd_line: Optional[float]
    macd_signal: Optional[float]
    macd_hist: Optional[float]
    macd_status: MACDStatus
    volume_ratio: Optional[float]    # None if volume data missing
    volume_status: VolumeStatus
    support_levels: list[float] = field(default_factory=list)
    resistance_levels: list[float] = field(default_factory=list)
    signal_score: int = 0            # 0-100: overall actionability
    trend_direction_score: int = 0   # 0-70: MA+MACD only, drives TrendStatus

    def to_dict(self) -> dict:
        return {
            'trend_status': self.trend_status.value,
            'ma5': round(self.ma5, 4) if self.ma5 is not None else None,
            'ma10': round(self.ma10, 4) if self.ma10 is not None else None,
            'ma20': round(self.ma20, 4) if self.ma20 is not None else None,
            'ma_alignment_score': self.ma_alignment_score,
            'rsi_value': round(self.rsi_value, 2) if self.rsi_value is not None else None,
            'rsi_status': self.rsi_status.value,
            'macd_line': round(self.macd_line, 4) if self.macd_line is not None else None,
            'macd_signal': round(self.macd_signal, 4) if self.macd_signal is not None else None,
            'macd_hist': round(self.macd_hist, 4) if self.macd_hist is not None else None,
            'macd_status': self.macd_status.value,
            'volume_ratio': round(self.volume_ratio, 2) if self.volume_ratio is not None else None,
            'volume_status': self.volume_status.value,
            'support_levels': [round(v, 2) for v in self.support_levels],
            'resistance_levels': [round(v, 2) for v in self.resistance_levels],
            'signal_score': self.signal_score,
            'trend_direction_score': self.trend_direction_score,
        }

    def to_compact_str(self) -> str:
        if self.ma_alignment_score == 2:
            ma_str = 'MA5>MA10>MA20'
        elif self.ma_alignment_score == 1:
            # Determine which partial alignment is active
            if self.ma5 is not None and self.ma10 is not None and self.ma5 > self.ma10:
                ma_str = 'MA5>MA10'
            elif self.ma10 is not None and self.ma20 is not None and self.ma10 > self.ma20:
                ma_str = 'MA10>MA20'
            else:
                ma_str = 'MA(partial)'
        else:
            ma_str = 'MA5<MA10<MA20'
        rsi_str = f'{round(self.rsi_value, 1)}' if self.rsi_value is not None else 'N/A'
        if self.volume_ratio is not None:
            vol_str = f'{round(self.volume_ratio, 1)}x({self.volume_status.value})'
        else:
            vol_str = 'N/A'
        return (
            f'Trend:{self.trend_status.value} {ma_str} '
            f'RSI={rsi_str}({self.rsi_status.value}) '
            f'MACD={self.macd_status.value} '
            f'Vol={vol_str} '
            f'Score={self.signal_score}'
        )
