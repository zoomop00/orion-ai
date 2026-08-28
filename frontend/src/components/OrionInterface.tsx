"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { useOrionTurn } from "@/hooks/use-orion-turn";
import { ORION_VISUAL_STATES, nextInSequence, profileFor } from "@/state/orion-visual-state";

import { OrionAnalysisDeck } from "./OrionAnalysisDeck";
import { OrionCore } from "./OrionCore";
import styles from "./OrionInterface.module.css";

/**
 * The whole ORION surface.
 *
 * Still deliberately almost empty: an identity mark, the core, one line of
 * status, one command surface, and a development state switcher. Mission
 * 10's brief is explicit that the UI *is* ORION — every panel added here is
 * a panel competing with the thing the user is supposed to be looking at.
 *
 * PHASE 3 ADDED ONE THING: a single command line. Not a chat log, not
 * message bubbles, not a transcript history, not a sidebar, not a card. One
 * input and one send affordance, both visually subordinate to the core. The
 * last exchange is shown as two lines in the readout that already existed —
 * what ORION heard, and what ORION said — and it is replaced by the next
 * turn rather than accumulated.
 *
 * PHASE 4 ADDS NO SURFACE AT ALL. The microphone button was already here;
 * it now does what it always looked like it did. There is no waveform
 * widget, no level meter, no recording timer and no permission banner —
 * the core *is* the level meter, and it is driven by `amplitude` below. The
 * one thing that changed shape is the button's own label, which used to
 * disclaim a microphone that now exists.
 *
 * PHASE 5.4 ADDS ONE CONDITIONAL REGION. When a turn produced an analysis --
 * which it does only when the user actually asked for one -- `OrionAnalysisDeck`
 * renders beneath the core. It is not a route, not a panel that can be opened,
 * and not a state: there is nothing to toggle, because the deck exists exactly
 * when an analysis does and vanishes with it on the next turn. An ordinary
 * conversational turn still renders the Phase 4 interface unchanged, which is
 * what keeps the brief's "spacious environment, not a wall of panels" true at
 * rest.
 *
 * THE CORE IS STILL THE SUBJECT. The deck goes below the core, never beside
 * it, and `OrionCore` was not touched: it keeps its full `clamp(230px, 58vmin,
 * 540px)` stage rather than being shrunk to make room for data. On a short
 * viewport the page grows and scrolls vertically instead, which is the honest
 * trade -- squeezing the core to fit a report would invert the hierarchy the
 * whole mission is built around.
 *
 * ALL BACKEND TEXT IS RENDERED AS TEXT. `transcript`, `responseText`, and now
 * the analysis's `rationale` and capability details, are interpolated as JSX
 * children, which React escapes. There is no `dangerouslySetInnerHTML`, no
 * `innerHTML`, no markdown parser and no HTML string construction anywhere in
 * this tree, so a hostile transcript is displayed rather than executed.
 */
export function OrionInterface() {
  const {
    visualState,
    transcript,
    responseText,
    analysis,
    error,
    busy,
    amplitude,
    capturing,
    submit,
    startListening,
    stopListening,
    setVisualState,
  } = useOrionTurn();
  const profile = profileFor(visualState);
  // The permission prompt is open: busy, but ORION is not listening yet and
  // the interface does not pretend otherwise.
  const arming = busy && !capturing && visualState === "idle";

  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const wasBusy = useRef(false);

  // When a turn finishes, hand the caret back so the next command can be
  // typed straight away — after a success and after a failure alike.
  useEffect(() => {
    if (wasBusy.current && !busy) inputRef.current?.focus();
    wasBusy.current = busy;
  }, [busy]);

  const onSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      // Whitespace-only input never reaches the network; `submit` refuses it
      // too, so the guard exists in both places on purpose.
      if (draft.trim().length === 0 || busy) return;
      submit(draft);
      setDraft("");
    },
    [draft, busy, submit],
  );

  const toggleMicrophone = useCallback(() => {
    if (capturing) stopListening();
    else startListening();
  }, [capturing, startListening, stopListening]);

  const sendDisabled = busy || draft.trim().length === 0;

  return (
    <main className={styles.shell} data-state={visualState} data-testid="orion-interface">
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
        <span className={styles.build}>Mission 10 &middot; Phase 5.4 &middot; cinematic analysis</span>
      </header>

      {/*
        `data-analysis` exists for the stylesheet, not for logic. At rest the
        centre row is a fixed-height stage that centres the core; with a deck
        in it the row has to be allowed to grow instead, or `.shell`'s
        `overflow: hidden` would clip the report rather than let the page
        scroll to it.
      */}
      <section className={styles.centre} data-analysis={analysis !== null}>
        <OrionCore state={visualState} amplitude={amplitude ?? undefined} />
        {analysis !== null && <OrionAnalysisDeck analysis={analysis} />}
      </section>

      <footer className={styles.footer}>
        <p className={styles.readout} aria-live="polite" data-testid="orion-status">
          <span className={styles.stateLabel}>{profile.label}</span>
          {transcript !== null && (
            <span className={styles.transcript} data-testid="orion-transcript">
              {transcript}
            </span>
          )}
          <span className={styles.statusText} data-testid="orion-response">
            {responseText ?? profile.status}
          </span>
        </p>

        {error !== null && (
          <p className={styles.error} role="alert" data-testid="orion-error">
            {error}
          </p>
        )}

        <div className={styles.commandRow}>
          <form className={styles.command} onSubmit={onSubmit} data-testid="orion-command">
            <input
              ref={inputRef}
              type="text"
              name="command"
              className={styles.commandInput}
              data-testid="orion-command-input"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Ask ORION"
              aria-label="Ask ORION"
              disabled={busy}
              // Off so the browser never files a typed command away in
              // autofill history — the one place this interface could
              // accidentally persist what the user said.
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="sentences"
              spellCheck={false}
              enterKeyHint="send"
              // A courtesy stop, not the rule: Mission 9's `Transcript`
              // remains the authority on acceptable text and re-checks it.
              maxLength={2000}
            />
            <button
              type="submit"
              className={styles.commandSend}
              data-testid="orion-command-send"
              disabled={sendDisabled}
              aria-label="Send to ORION"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                <path
                  d="M12 19V6M12 6l-5.2 5.2M12 6l5.2 5.2"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          </form>

          <button
            type="button"
            className={styles.mic}
            data-testid="orion-mic"
            data-capturing={capturing}
            aria-pressed={capturing}
            aria-busy={arming}
            // Enabled while capturing, because this is the control that ends
            // the recording; locked whenever a turn is running without one.
            disabled={busy && !capturing}
            aria-label={
              capturing
                ? "Stop listening and send what ORION heard."
                : arming
                  ? "Waiting for microphone permission."
                  : "Listen. ORION will ask for permission to use the microphone."
            }
            onClick={toggleMicrophone}
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
        </div>

        <div className={styles.dev} role="group" aria-label="Development state controls">
          <span className={styles.devLabel}>Dev</span>
          {ORION_VISUAL_STATES.map((candidate) => (
            <button
              key={candidate}
              type="button"
              className={styles.devButton}
              data-testid={`state-${candidate}`}
              aria-pressed={candidate === visualState}
              disabled={busy}
              onClick={() => setVisualState(candidate)}
            >
              {profileFor(candidate).label}
            </button>
          ))}
          <button
            type="button"
            className={`${styles.devButton} ${styles.devAdvance}`}
            data-testid="state-advance"
            disabled={busy}
            onClick={() => setVisualState(nextInSequence(visualState))}
          >
            Next
          </button>
        </div>

        <p className={styles.disclosure}>
          Local prototype &mdash; talks only to ORION on this machine. The microphone runs only
          while you are speaking a turn, and is released the moment you stop. Nothing is
          recorded, stored or logged.
        </p>
      </footer>
    </main>
  );
}
