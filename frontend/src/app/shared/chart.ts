import { AfterViewInit, Component, ElementRef, Input, OnChanges, OnDestroy, ViewChild } from '@angular/core';
import { Chart, ChartConfiguration, registerables } from 'chart.js';

Chart.register(...registerables);
Chart.defaults.font.family = "system-ui, -apple-system, 'Segoe UI', sans-serif";
Chart.defaults.color = '#5b6577';

/** Thin wrapper around Chart.js: updates in place when the config changes so polling doesn't flicker. */
@Component({
  selector: 'app-chart',
  template: `<div class="chart-box" [style.height.px]="height"><canvas #canvas></canvas></div>`,
  styles: [`.chart-box { position: relative; width: 100%; }`],
})
export class ChartComponent implements AfterViewInit, OnChanges, OnDestroy {
  @Input({ required: true }) config!: ChartConfiguration;
  @Input() height = 300;
  @ViewChild('canvas') private canvas!: ElementRef<HTMLCanvasElement>;
  private chart?: Chart;

  ngAfterViewInit() {
    this.render();
  }
  ngOnChanges() {
    if (this.chart) this.render();
  }
  ngOnDestroy() {
    this.chart?.destroy();
  }

  private render() {
    if (this.chart && (this.chart.config as ChartConfiguration).type === this.config.type) {
      this.chart.data = this.config.data;
      this.chart.options = this.config.options ?? {};
      this.chart.update('none');
      return;
    }
    this.chart?.destroy();
    this.chart = new Chart(this.canvas.nativeElement, this.config);
  }
}
