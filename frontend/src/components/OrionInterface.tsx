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
 * ORION IS NOT AN ORB. The core is ORION's visual centre; the screen is the
 * environment it occupies. Two earlier revisions got this wrong in opposite
 * directions -- one stacked core, caption, reply and command line down the
 * middle as separate blocks, so an answer had to steal height from ORION's
 * identity; the next split the screen into ORION on the left and a paragraph
 * on the right, which fixed the height problem and read as a two-column
 * landing page. Both failed for the same reason: they treated the reply as a
 * region that needed its own real estate.
 *
 * SO THERE IS ONE AXIS, AND EVERYTHING IS ON IT. Ambient field, core,
 * response, command line: one centred composition, read top to bottom, with
 * nothing beside anything. The reply is not given a column or a band -- it
 * rises into the lower part of the core's own field (see `--core-overlap` in
 * the stylesheet), so ORION and what ORION says are one object rather than
 * two stacked ones. That overlap is also what buys the core the room to be
 * substantially larger than either previous revision allowed.
 *
 * THE CORE NEVER SHRINKS TO MAKE ROOM FOR TEXT. `--orion-core-stage` is a
 * function of the viewport and of nothing else -- not of whether a reply
 * arrived, not of whether an analysis is on screen. When a report is too
 * tall for the field, the core stays exactly where and what it is and the
 * report scrolls beneath it.
 *
 * NOTHING IS FABRICATED TO LOOK ALIVE. There is no typewriter, no simulated
 * stream, no artificial delay and no fake progress. The sequence a user
 * feels -- their words acknowledged, ORION working, an answer resolving --
 * is the real lifecycle in `use-orion-turn`, rendered: `prompt` echoes the
 * input the instant it is sent, `thinking` lasts exactly as long as the
 * request does, and the reply's arrival is a blur-to-focus on text that was
 * already complete when it got here.
 *
 * ALL BACKEND TEXT IS RENDERED AS TEXT. `transcript`, `responseText`, and the
 * analysis's own strings are interpolated as JSX children, which React
 * escapes. There is no `dangerouslySetInnerHTML`, no `innerHTML`, no markdown
 * parser and no HTML string construction anywhere in this tree, so a hostile
 * transcript is displayed rather than executed.
 */
export function OrionInterface() {
  const {
    visualState,
    transcript,
    prompt,
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

  /**
   * What this turn is about, from whichever source knows first.
   *
   * `transcript` is ORION's own report of what it understood and always wins
   * once it exists. `prompt` is the user's typed text, shown in the gap
   * before that -- so the turn reads as addressed from the keystroke rather
   * than only once the answer lands. They occupy the same line but are never
   * the same claim, which is why the element below is named for whichever
   * one is actually being shown.
   */
  const addressed = transcript ?? prompt;
  const analysing = analysis !== null;

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
    <main
      className={styles.shell}
      data-state={visualState}
      data-analysis={analysing}
      data-testid="orion-interface"
    >
      {/*
        The environment, in four layers and no more, all centred on the same
        axis as the core: the light ORION casts into the room, a far
        counter-light so the space has more than one source, a very sparse
        depth field, and the falloff at the edges. There is no horizon rule,
        no corner bracket and no measurement chrome -- those read as HUD
        decoration, which is the opposite of atmosphere.
      */}
      <div className={styles.atmosphere} aria-hidden="true">
        <div className={styles.depth} />
        <div className={styles.ambient} />
        <div className={styles.counterLight} />
        <div className={styles.falloff} />
      </div>

      <header className={styles.header}>
        <div className={styles.identity}>
          <span className={styles.wordmark}>ORION</span>
          <span className={styles.tagline}>Autonomous Market Intelligence</span>
        </div>
        <span className={styles.build}>Mission 10 &middot; Phase 6 &middot; spatial composition</span>
      </header>

      {/*
        One composition, not two regions. `.voice` tucks up into the lower
        part of the core's field, so what ORION says reads as coming out of
        ORION rather than as a block placed underneath it.
      */}
      <div className={styles.field}>
        <div className={styles.coreZone}>
          <OrionCore state={visualState} amplitude={amplitude ?? undefined} />
        </div>

        {/*
          `data-analysis` is the stylesheet's hook and the deck's own parent,
          so a report belongs to this element rather than being appended to
          the page under the core. This is the only thing on screen allowed
          to scroll, and an ordinary reply never makes it do so.
        */}
        <section className={styles.voice} data-analysis={analysing}>
          {addressed !== null && (
            <p
              className={styles.address}
              /*
                The two are different claims and are named differently, so a
                test asserting on what ORION *understood* can never be
                satisfied by the echo of what the user typed.
                `orion-transcript` still means, and only means, a transcript
                the backend sent.
              */
              data-testid={transcript !== null ? "orion-transcript" : "orion-prompt"}
            >
              {addressed}
            </p>
          )}

          <p className={styles.reply} aria-live="polite" data-testid="orion-status">
            <span className={styles.stateLabel}>{profile.label}</span>
            {/*
              Keyed on the text itself, so React remounts this node whenever
              ORION says something new and the resolve animation replays.
              Nothing is streamed or fabricated -- the whole sentence is
              already here; this only governs how it arrives on screen.
            */}
            <span
              key={responseText ?? profile.status}
              className={styles.statusText}
              data-testid="orion-response"
            >
              {responseText ?? profile.status}
            </span>
          </p>

          {error !== null && (
            <p className={styles.error} role="alert" data-testid="orion-error">
              {error}
            </p>
          )}

          {analysis !== null && <OrionAnalysisDeck analysis={analysis} />}
        </section>

        {/*
          The user's channel into the field, on the same axis and inside the
          same group as the core and the reply -- not a footer under them. A
          caret and a hairline rather than a bordered pill: this is how ORION
          is addressed, and it stays visually subordinate to the core.
        */}
        <div className={styles.commandRow}>
          <form className={styles.command} onSubmit={onSubmit} data-testid="orion-command">
            <span className={styles.caret} aria-hidden="true" />
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
      </div>

      {/*
        Development affordances. Absolutely positioned out of the grid
        entirely, so they occupy no space in the composition and cannot
        influence it at any viewport -- they rest at a fraction of their
        weight in the corner and come up on hover or keyboard focus.
      */}
      <div className={styles.aside}>
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
      </div>
    </main>
  );
}
