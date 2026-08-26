export class SpeechActivityAdapter {
  private smoothed = 0;
  private active = false;
  update(decibels?: number): number {
    if (typeof decibels !== 'number' || !Number.isFinite(decibels)) return this.reset();
    const normalized = Math.max(0, Math.min(1, (decibels + 55) / 45));
    const gate = this.active ? 0.09 : 0.16;
    this.active = normalized >= gate;
    const target = this.active ? (normalized - gate) / (1 - gate) : 0;
    const coefficient = target > this.smoothed ? 0.42 : 0.16;
    this.smoothed += (target - this.smoothed) * coefficient;
    if (this.smoothed < 0.015) this.smoothed = 0;
    return this.smoothed;
  }
  reset(): number { this.active = false; this.smoothed = 0; return 0; }
}
