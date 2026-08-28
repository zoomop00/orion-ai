"use client";

import { useMemo, type CSSProperties } from "react";

import { toAnalysisView, type AnalysisReading } from "@/lib/analysis-view";
import type { OrionAnalysis } from "@/lib/orion-analysis";

import styles from "./OrionAnalysisDeck.module.css";

/**
 * Same shape `OrionCore` uses: TypeScript decides the numbers, CSS decides
 * what they look like at a given viewport size.
 */
type DeckStyle = CSSProperties & Record<`--${string}`, string | number>;

/**
 * The analysis, drawn.
 *
 * WHAT PHASE 5.3 LEFT HERE. `parseAnalysis` proved every field; `toAnalysisView`
 * decided what each one is called and how it reads. This component's entire job
 * is placing already-final strings into elements. It contains no rounding, no
 * fallback, no branch on a raw enum and no arithmetic beyond handing the
 * confidence fraction to CSS -- if a decision about *what* to show appears in
 * this file in a later phase, it is in the wrong file.
 *
 * A READOUT, NOT A DASHBOARD. The brief's north star is "clean, cinematic
 * command center ... not a wall of panels", and explicitly warns off crowded
 * trading dashboards and generic SaaS cards. So: no table, no chart, no
 * canvas, no card with four borders and a drop shadow. Each panel is a hairline
 * rule and a stack of label/value pairs on the same deep-space ground the rest
 * of the interface uses, tinted by the same `--orion-accent-rgb` the shell
 * already sets per visual state. The result reads as something projected by
 * ORION rather than as a page ORION happens to be on.
 *
 * THE CORE STAYS THE SUBJECT. This deck renders *below* the core, never
 * alongside it and never over it, and it exists only while an analysis does.
 * An ordinary conversational turn -- which is most of them -- renders nothing
 * from this file at all, so the resting interface is byte-identical to Phase 4.
 *
 * EVERY BACKEND STRING IS JSX TEXT. `rationale` and the two capability details
 * are the only free text the backend can put on this screen, and both are
 * interpolated as children, which React escapes. There is no
 * `dangerouslySetInnerHTML`, no markdown parser, no HTML construction and no
 * `href`, `src` or `style` string built from a server value anywhere in this
 * tree. The one server-derived value that reaches CSS is `confidence`, a number
 * the parser already bounded to 0..1, and it reaches it as a custom property
 * rather than as a style string.
 *
 * ABSENCE IS DRAWN AS ABSENCE. A reading the backend sent as `null` gets the em
 * dash plus a visually hidden "not available", and `data-available="false"`
 * for the stylesheet to dim it. It never gets a zero, a placeholder number or a
 * blank cell that could be read as either.
 */

export interface OrionAnalysisDeckProps {
  /** Non-null by construction: the parent renders nothing when there is none. */
  readonly analysis: OrionAnalysis;
}

function Readings({ readings, testId }: { readings: readonly AnalysisReading[]; testId: string }) {
  return (
    <dl className={styles.readings} data-testid={testId}>
      {readings.map((entry) => (
        <div key={entry.label} className={styles.reading} data-available={entry.available}>
          <dt className={styles.readingLabel}>{entry.label}</dt>
          <dd className={styles.readingValue}>
            <span className={styles.value}>{entry.value}</span>
            {!entry.available && <span className={styles.srOnly}> not available</span>}
            {entry.hint !== undefined && <span className={styles.hint}>{entry.hint}</span>}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function OrionAnalysisDeck({ analysis }: OrionAnalysisDeckProps) {
  // The analysis object is replaced wholesale by the reducer, never mutated,
  // so identity is a sound cache key -- and this runs on a tree that is
  // re-rendered up to sixty times a second by the core's amplitude.
  const view = useMemo(() => toAnalysisView(analysis), [analysis]);

  return (
    <section
      className={styles.deck}
      data-testid="orion-analysis"
      aria-label={`ORION analysis of ${view.target.symbol}, ${view.target.timeframe}`}
    >
      <p className={styles.target} data-testid="orion-analysis-target">
        <span className={styles.symbol}>{view.target.symbol}</span>
        <span className={styles.divider} aria-hidden="true" />
        <span className={styles.timeframe}>{view.target.timeframe}</span>
        <span className={styles.divider} aria-hidden="true" />
        <span className={styles.price} data-testid="orion-analysis-price">
          {view.target.lastPrice}
        </span>
        <span className={styles.divider} aria-hidden="true" />
        <time className={styles.instant} dateTime={view.target.asOf.machine}>
          {view.target.asOf.display}
        </time>
        <span className={styles.bars}>{view.target.barCount} bars</span>
      </p>

      <div className={styles.verdict} data-testid="orion-analysis-verdict">
        <p className={styles.action}>
          <span className={styles.actionWord} data-action={analysis.conclusion.action}>
            {view.conclusion.action}
          </span>
          <span className={styles.bias}>{view.conclusion.direction}</span>
        </p>

        <p className={styles.rationale} data-testid="orion-analysis-rationale">
          {view.conclusion.rationale}
        </p>

        <div className={styles.gauges}>
          <div className={styles.gauge}>
            <span className={styles.gaugeLabel}>Confidence</span>
            <div
              className={styles.meter}
              role="meter"
              aria-label="Confidence"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(view.conclusion.confidenceFraction * 100)}
              aria-valuetext={view.conclusion.confidence}
              data-testid="orion-analysis-confidence"
              // A bounded 0..1 number handed to CSS as a custom property. The
              // fill is drawn with `transform: scaleX()`, so it composites on
              // the GPU and never triggers layout.
              style={{ "--confidence": view.conclusion.confidenceFraction } as DeckStyle}
            >
              <span className={styles.meterFill} aria-hidden="true" />
            </div>
            <span className={styles.gaugeValue}>{view.conclusion.confidence}</span>
          </div>

          <div className={styles.gauge}>
            <span className={styles.gaugeLabel}>Risk level</span>
            <span
              className={styles.riskLevel}
              data-level={analysis.riskLevel}
              data-testid="orion-analysis-risk-level"
            >
              {view.conclusion.riskLevel}
            </span>
          </div>
        </div>

        {view.conclusion.reasonCodes.length > 0 && (
          <ul className={styles.codes} data-testid="orion-analysis-reason-codes">
            {view.conclusion.reasonCodes.map((code) => (
              <li key={code} className={styles.code}>
                {code}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className={styles.panels}>
        <article className={styles.panel} data-testid="orion-panel-structure">
          <h2 className={styles.panelTitle}>Market structure</h2>
          <Readings readings={view.structure} testId="orion-readings-structure" />
        </article>

        <article className={styles.panel} data-testid="orion-panel-momentum">
          <h2 className={styles.panelTitle}>Momentum</h2>
          <Readings readings={view.momentum} testId="orion-readings-momentum" />
        </article>

        <article className={styles.panel} data-testid="orion-panel-context">
          <h2 className={styles.panelTitle}>External context</h2>
          {view.capabilities.map((capability) => (
            <div
              key={capability.id}
              className={styles.capability}
              data-testid={`orion-capability-${capability.id}`}
            >
              <span className={styles.capabilityLabel}>{capability.label}</span>
              <span className={styles.capabilityStatus}>{capability.status}</span>
              <span className={styles.capabilityDetail}>{capability.detail}</span>
            </div>
          ))}
        </article>

        <article
          className={styles.panel}
          data-testid="orion-panel-risk"
          data-available={view.risk.available}
        >
          <h2 className={styles.panelTitle}>Risk guardian</h2>
          <p className={styles.riskHeadline} data-testid="orion-risk-headline">
            {view.risk.headline}
          </p>
          <p className={styles.riskExplanation} data-testid="orion-risk-explanation">
            {view.risk.explanation}
          </p>

          {view.risk.failedControls.length > 0 && (
            <ul className={styles.codes} data-testid="orion-risk-failed-controls">
              {view.risk.failedControls.map((control) => (
                <li key={control} className={`${styles.code} ${styles.codeFailed}`}>
                  {control}
                </li>
              ))}
            </ul>
          )}

          {view.risk.reasonCodes.length > 0 && (
            <ul className={styles.codes} data-testid="orion-risk-reason-codes">
              {view.risk.reasonCodes.map((code) => (
                <li key={code} className={styles.code}>
                  {code}
                </li>
              ))}
            </ul>
          )}

          {view.risk.portfolioAsOf !== null && (
            <p className={styles.riskInstant}>
              <span className={styles.readingLabel}>Account as of</span>
              <time dateTime={view.risk.portfolioAsOf.machine}>
                {view.risk.portfolioAsOf.display}
              </time>
            </p>
          )}
        </article>
      </div>
    </section>
  );
}
