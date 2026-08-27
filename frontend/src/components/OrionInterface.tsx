"use client";

import { useCallback, useState } from "react";

import {
  ORION_VISUAL_STATES,
  nextInSequence,
  profileFor,
  type OrionVisualState,
} from "@/state/orion-visual-state";

import { OrionCore } from "./OrionCore";
import styles from "./OrionInterface.module.css";

/**
 * The whole ORION surface.
 *
 * Deliberately almost empty: an identity mark, the core, one line of status,
 * one control affordance, and a development state switcher. Mission 10's brief
 * is explicit that the UI *is* ORION -- every panel added here is a panel
 * competing with the thing the user is supposed to be looking at.
 *
 * There is no data, no chart, no card and no network call anywhere in this
 * tree. Phase 1 renders ORION, not ORION's output.
 */
export function OrionInterface() {
  const [state, setState] = useState<OrionVisualState>("idle");
  const profile = profileFor(state);
  const listening = state === "listening";

  const toggleListening = useCallback(() => {
    setState((current) => (current === "listening" ? "idle" : "listening"));
  }, []);

  const advance = useCallback(() => {
    setState(nextInSequence);
  }, []);

  return (
    <main className={styles.shell} data-state={state} data-testid="orion-interface">
      <div className={styles.field} aria-hidden="true">
        <div className={styles.aurora} />
        <div className={styles.grid} />
        <div className={styles.vignette} />
      </div>

      <header className={styles.header}>
        <div className={styles.identity}>
          <span className={styles.wordmark}>ORION</span>
          <span className={styles.tagline}>Autonomous Market Intelligence</span>
        </div>
        <span className={styles.build}>Mission 10 &middot; Phase 1 &middot; visual prototype</span>
      </header>

      <section className={styles.centre}>
        <OrionCore state={state} />
      </section>

      <footer className={styles.footer}>
        <p className={styles.readout} aria-live="polite" data-testid="orion-status">
          <span className={styles.stateLabel}>{profile.label}</span>
          <span className={styles.statusText}>{profile.status}</span>
        </p>

        <button
          type="button"
          className={styles.mic}
          data-testid="orion-mic"
          aria-pressed={listening}
          aria-label={
            listening
              ? "Leave the listening state. No microphone is connected in this prototype."
              : "Enter the listening state. No microphone is connected in this prototype."
          }
          onClick={toggleListening}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path
              d="M12 4.4a2.7 2.7 0 0 1 2.7 2.7v5a2.7 2.7 0 0 1-5.4 0v-5A2.7 2.7 0 0 1 12 4.4Z"
              fill="currentColor"
              opacity="0.9"
            />
            <path
              d="M6.4 11.4v0.7a5.6 5.6 0 0 0 11.2 0v-0.7M12 17.7v2.1"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinecap="round"
            />
          </svg>
        </button>

        <div className={styles.dev} role="group" aria-label="Development state controls">
          <span className={styles.devLabel}>Dev</span>
          {ORION_VISUAL_STATES.map((candidate) => (
            <button
              key={candidate}
              type="button"
              className={styles.devButton}
              data-testid={`state-${candidate}`}
              aria-pressed={candidate === state}
              onClick={() => setState(candidate)}
            >
              {profileFor(candidate).label}
            </button>
          ))}
          <button
            type="button"
            className={`${styles.devButton} ${styles.devAdvance}`}
            data-testid="state-advance"
            onClick={advance}
          >
            Next
          </button>
        </div>

        <p className={styles.disclosure}>
          Visual prototype &mdash; no microphone, no network, no stored data.
        </p>
      </footer>
    </main>
  );
}
