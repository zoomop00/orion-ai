"use client";

import { useMemo, useRef, type CSSProperties } from "react";

import { useOrionTempo } from "@/hooks/use-orion-tempo";
import {
  DUST,
  DUST_PLANES,
  FLOOR_RINGS,
  NETWORK_EDGES,
  NETWORK_NODES,
  RIBBONS,
  RIBBONS_FRONT,
  SHAFTS,
  type RibbonLayout,
} from "@/lib/field-geometry";
import { profileFor, type OrionVisualState } from "@/state/orion-visual-state";

import styles from "./OrionField.module.css";

/** Same convention `OrionCore` uses: TypeScript sets numbers, CSS sets looks. */
type FieldStyle = CSSProperties & Record<`--${string}`, string | number>;

/**
 * The chamber.
 *
 * ORION IS NOT AN ORB ON A PAGE, AND THIS FILE IS WHY. Every earlier revision
 * put a very carefully built core in the middle of a dark rectangle and then
 * wondered why the result felt like a graphic rather than a place. The core
 * was never the problem: there was nothing for it to be inside. This
 * component is the inside -- light falling through a large volume of air,
 * waves crossing it, a graph standing off in it, a floor for the light to
 * land on, and dust to give the black a scale.
 *
 * IT IS PURELY ENVIRONMENTAL. Nine layers, no text, no control, no readout,
 * `aria-hidden` on the root, `pointer-events: none` throughout, and
 * `position: absolute` on every one of them. It cannot take focus, cannot be
 * read aloud, and cannot move a single pixel of layout at any viewport --
 * which is precisely what allows it to be this large and this busy while the
 * composition's invariants hold exactly as they did before it existed.
 *
 * NOTHING HERE IS DATA. Not one node, edge, ribbon, mote or ring stands for a
 * measurement, a service, a market or a system state. There is no telemetry
 * in this build and this is not a disguised readout of one -- the room says
 * ORION is present and working, and ORION's own words say everything else.
 * That distinction is the reason there is not a single number on screen.
 *
 * THE MOTION IS UNSYNCHRONISED ON PURPOSE. Ribbon drifts of 71, 89, 113, 137,
 * 167, 191 and 211 seconds share no common factor worth speaking of, and the
 * sway, flicker, signal and floor periods are unrelated again. The room
 * therefore never presents the same frame twice inside any span a person will
 * watch it for, which is the entire difference between "alive" and "looping".
 */
export interface OrionFieldProps {
  readonly state: OrionVisualState;
}

/**
 * How much of the core's tempo change the room takes.
 *
 * The room is a hundred times the core's area, so matching its acceleration
 * exactly makes THINKING look like the page is being fast-forwarded rather
 * than like ORION is working harder. Just over half the shift is enough to be
 * felt across the whole screen and small enough that the core stays the thing
 * that visibly changed gear.
 */
const ENVIRONMENT_TEMPO_SHARE = 0.55;

/** Near plane last, so the nearest nodes paint over the far ones. */
const NETWORK_DEPTHS = [0, 1, 2] as const;

export function OrionField({ state }: OrionFieldProps) {
  const chamberRef = useRef<HTMLDivElement>(null);
  const profile = profileFor(state);

  /*
   * The same easing the core uses, on the same state model, damped. Changing
   * `animation-duration` in CSS restarts every clock, which would make the
   * whole room stutter on a state change; `useOrionTempo` ramps
   * `playbackRate` instead, so the waves keep exactly where they are and only
   * change speed. This is a second caller of an existing hook, not a second
   * animation system.
   */
  useOrionTempo(chamberRef, 1 + (profile.tempo - 1) * ENVIRONMENT_TEMPO_SHARE);

  /*
   * Every layer below depends on module constants and nothing else. The core
   * above re-renders up to sixty times a second from live audio and this
   * component sits in the same tree, so memoising is what stops ~140 nodes,
   * edges, ribbons and motes from being reconciled every frame for no reason.
   * Only the root's `data-state` actually changes, and it changes rarely.
   */
  const shafts = useMemo(
    () => (
      <div className={styles.shafts}>
        {SHAFTS.map((shaft, index) => (
          <span
            key={index}
            className={styles.shaft}
            style={
              {
                "--x": `${shaft.x}%`,
                "--w": `${shaft.width}vw`,
                "--h": `${shaft.height}%`,
                "--alpha": shaft.opacity,
                "--spin": `${shaft.duration}s`,
                "--offset": `${shaft.delay}s`,
              } as FieldStyle
            }
          />
        ))}
      </div>
    ),
    [],
  );

  /*
   * Dust, in three planes, and the plane is what moves.
   *
   * Every mote used to drift on a clock of its own. Thirty of them is thirty
   * animations for something the eye reads as one thing -- air with depth in
   * it -- and parallax does not come from motes disagreeing with each other,
   * it comes from near ones crossing faster than far ones. Three transforms
   * give exactly that.
   */
  const dust = useMemo(
    () => (
      <div className={styles.dust} data-testid="orion-dust">
        {DUST_PLANES.map((plane) => (
          <div
            key={plane.depth}
            className={styles.dustPlane}
            data-depth={plane.depth}
            style={
              {
                "--spin": `${plane.duration}s`,
                "--offset": `${plane.delay}s`,
                "--drift": `${plane.drift}%`,
              } as FieldStyle
            }
          >
            {DUST.filter((mote) => mote.depth === plane.depth).map((mote, index) => (
              <span
                key={index}
                className={styles.mote}
                data-depth={mote.depth}
                style={
                  { "--x": `${mote.x}%`, "--y": `${mote.y}%`, "--alpha": mote.opacity } as FieldStyle
                }
              />
            ))}
          </div>
        ))}
      </div>
    ),
    [],
  );

  const floor = useMemo(
    () => (
      <div className={styles.floor}>
        <div className={styles.column} />
        <div className={styles.pool} />
        <svg
          className={styles.floorRings}
          viewBox="0 0 200 60"
          preserveAspectRatio="none"
          focusable="false"
        >
          {FLOOR_RINGS.map((ring, index) => (
            <ellipse
              key={index}
              className={styles.floorRing}
              cx={100}
              cy={30}
              rx={ring.rx}
              ry={ring.ry}
              style={
                {
                  "--alpha": ring.opacity,
                  "--weight": ring.width,
                  "--spin": `${ring.duration}s`,
                  "--offset": `${ring.delay}s`,
                } as FieldStyle
              }
            />
          ))}
        </svg>
      </div>
    ),
    [],
  );

  /*
   * The waves. Each ribbon is drawn across two periods and its lane is
   * translated by exactly one, so the loop has no seam; the two nested
   * wrappers carry the horizontal drift and the vertical sway on unrelated
   * periods, so a ribbon never repeats its own path across the screen.
   *
   * THE LANE IS AN ELEMENT, NOT A GROUP, AND THAT IS THE WHOLE POINT. See
   * `Ribbon` below: this container is a plain box holding seven independent
   * lanes rather than one `<svg>` holding seven animated `<g>`s.
   */
  const ribbons = useMemo(
    () => (
      <div className={styles.ribbons} data-testid="orion-ribbons">
        {RIBBONS.map((ribbon, index) => (
          <Ribbon key={index} ribbon={ribbon} />
        ))}
      </div>
    ),
    [],
  );

  /*
   * The same current, in front of ORION.
   *
   * Two of the back field's own ribbons, drawn over the core instead of under
   * it. Everything else in the chamber is behind ORION, which is right for a
   * room and wrong for a body -- with nothing crossing it the core reads as
   * the front layer of a flat stack. One occlusion fixes that outright.
   *
   * It is masked to a band around the core's axis and fades out well above
   * where ORION speaks, so it never touches the reply. It also takes no
   * pointer events, so the command line underneath stays fully operable.
   *
   * IT CARRIES THE STATE TOO. It sits outside `.chamber` for stacking
   * reasons, which meant the one layer crossing ORION was also the one layer
   * that did not answer when ORION changed state -- the room would lean in
   * and the nearest thing to the viewer would carry on exactly as before. It
   * takes `data-state` for itself, so the whole field responds as one.
   */
  const ribbonsFront = useMemo(
    () => (
      <div className={styles.ribbonsFront} data-testid="orion-ribbons-front">
        {RIBBONS_FRONT.map((ribbon, index) => (
          <Ribbon key={index} ribbon={ribbon} />
        ))}
      </div>
    ),
    [],
  );

  /*
   * The intelligence field. A connection is one faint line; a signal is a
   * point running along it, animated with `transform` between the edge's two
   * endpoints -- which is exact for a straight line and stays on the
   * compositor, unlike the `stroke-dashoffset` sweep it replaces.
   */
  const network = useMemo(
    () => (
      <svg
        className={styles.network}
        viewBox="0 0 1000 600"
        preserveAspectRatio="xMidYMid meet"
        focusable="false"
        data-testid="orion-network"
      >
        <g className={styles.networkEdges}>
          {NETWORK_EDGES.map((edge, index) => (
            <g
              key={index}
              className={styles.link}
              style={
                {
                  "--alpha": edge.opacity,
                  "--life": `${edge.life}s`,
                  "--life-offset": `${edge.lifeDelay}s`,
                } as FieldStyle
              }
            >
              <line
                className={styles.linkLine}
                x1={edge.from.x}
                y1={edge.from.y}
                x2={edge.to.x}
                y2={edge.to.y}
              />
              {edge.carries && (
                <circle
                  className={styles.signal}
                  r={2.2}
                  style={
                    {
                      "--x1": edge.from.x,
                      "--y1": edge.from.y,
                      "--x2": edge.to.x,
                      "--y2": edge.to.y,
                      "--spin": `${edge.signal}s`,
                      "--offset": `${edge.delay}s`,
                    } as FieldStyle
                  }
                />
              )}
            </g>
          ))}
        </g>

        {/*
          Three depth groups, each breathing on its own long period, rather
          than fifty-two elements (a shell and a core per node) each scaling on
          a clock of its own. The nodes keep their individual resting weight;
          what they give up is disagreeing about when to flicker, which at this
          opacity nobody could resolve anyway.
        */}
        {NETWORK_DEPTHS.map((depth) => (
          <g key={depth} className={styles.nodeField} data-depth={depth}>
            {NETWORK_NODES.filter((node) => node.depth === depth).map((node, index) => (
              <g
                key={index}
                className={styles.node}
                style={{ "--alpha": node.opacity } as FieldStyle}
                transform={`translate(${node.x} ${node.y})`}
              >
                <polygon className={styles.nodeShell} points={hexagon(node.size)} />
                <path className={styles.nodeCube} d={cube(node.size)} />
              </g>
            ))}
          </g>
        ))}
      </svg>
    ),
    [],
  );

  return (
    <>
      <div ref={chamberRef} className={styles.chamber} data-state={state} aria-hidden="true">
        <div className={styles.deepVoid} />
        {shafts}
        {dust}
        <div className={styles.counterLight} />
        <div className={styles.ambient} />
        {floor}
        {ribbons}
        {network}
        <div className={styles.horizonGlow} />
        <div className={styles.falloff} />
      </div>
      {/*
        The state lives on the wrapper and the waves stay memoised inside it.
        Putting `data-state` on the memo itself would have frozen it at the
        state of the first render -- the attribute would never have changed
        and the layer would silently have stopped answering.
      */}
      <div className={styles.foreground} data-state={state} aria-hidden="true">
        {ribbonsFront}
      </div>
    </>
  );
}

/**
 * One ribbon: horizontal drift and vertical sway on unrelated periods.
 *
 * WHY THE MOTION IS OUTSIDE THE SVG, AND WHY THAT IS THE WHOLE FILE'S
 * PERFORMANCE STORY.
 *
 * These two wrappers used to be `<g>` elements inside one shared `<svg>`, and
 * the paths carry `vector-effect: non-scaling-stroke` because the viewBox is
 * stretched non-uniformly to the width of the screen. Those two facts
 * together are ruinous: a non-scaling stroke is defined against the element's
 * *current* accumulated transform, so animating a group that contains one
 * makes Blink re-derive fourteen stroked outlines and re-rasterise the entire
 * band -- about two megapixels -- on every single frame. Measured at
 * 1920x1080 it was the whole frame budget: 20.8ms per frame with the waves
 * moving, 7.0ms with the identical waves moving and only the vector effect
 * removed. Nothing else on screen -- not the halo, the lattice, the network,
 * the masks, the blend modes or the particle field -- was worth a millisecond
 * next to it.
 *
 * So the stroke stays exactly as it was and the *animation* moves out. A
 * plain box cannot have a non-scaling stroke and cannot invalidate one, so
 * the SVG inside is now completely static: it is rasterised once and the two
 * wrappers translate that finished texture on the compositor. The waves have
 * the same paths, the same widths, the same periods, the same easing and the
 * same phase offsets; what changed is who moves them.
 *
 * THE ARITHMETIC OF THE LANE. The path spans two periods (0..2000) and the
 * lane is 200% wide showing a 2000-unit viewBox, so one user unit is exactly
 * the pixel it was when the viewBox was 1000 units across a 100%-wide parent.
 * Drifting the lane by -50% of itself is therefore the same 1000 units the
 * group used to travel, and the sway's -9..9 user units are the same 2.25% of
 * a 400-unit box. Same numbers, expressed against the element instead of the
 * user space.
 */
function Ribbon({ ribbon }: { ribbon: RibbonLayout }) {
  return (
    <div
      className={styles.ribbonDrift}
      data-depth={ribbon.depth}
      style={
        {
          "--spin": `${ribbon.drift}s`,
          "--offset": `${ribbon.delay}s`,
          "--direction": ribbon.reverse ? "reverse" : "normal",
        } as FieldStyle
      }
    >
      <div className={styles.ribbonSway} style={{ "--sway": `${ribbon.sway}s` } as FieldStyle}>
        <svg
          className={styles.ribbonArt}
          viewBox="0 0 2000 400"
          preserveAspectRatio="none"
          focusable="false"
          aria-hidden="true"
        >
          {/*
            Two strokes on one path: a wide faint companion for the bloom, a
            thin bright core over it. The same look a drop-shadow gave, and now
            genuinely drawn once rather than once per frame.
          */}
          <path
            className={styles.ribbonGlow}
            d={ribbon.d}
            style={{ "--weight": ribbon.glow, "--alpha": ribbon.opacity } as FieldStyle}
          />
          <path
            className={styles.ribbon}
            d={ribbon.d}
            style={{ "--weight": ribbon.width, "--alpha": ribbon.opacity } as FieldStyle}
          />
        </svg>
      </div>
    </div>
  );
}

/**
 * A flat-topped hexagon of the given circumradius, centred on the origin.
 *
 * Written out rather than drawn with a `path` so the shape is legible in the
 * DOM and so the node's size stays a single number the geometry module owns.
 */
function hexagon(radius: number): string {
  const points: string[] = [];
  for (let i = 0; i < 6; i += 1) {
    const angle = (Math.PI / 3) * i;
    const x = Math.round(Math.cos(angle) * radius * 100) / 100;
    const y = Math.round(Math.sin(angle) * radius * 100) / 100;
    points.push(`${x},${y}`);
  }
  return points.join(" ");
}

/**
 * The three spokes that turn that hexagon into a cube seen corner-on.
 *
 * A hexagon with a point in the middle is a hexagon with a point in the
 * middle. A hexagon with lines from its centre to three alternating vertices
 * is the oldest isometric cube there is, and it is exactly the glyph the
 * reference uses for its network nodes -- the whole "blockchain, not
 * crypto-branded" cue in one path.
 *
 * It replaces the centre dot rather than joining it: same two elements per
 * node, same stroke budget, no new geometry to animate.
 */
function cube(radius: number): string {
  const inner = radius * 0.94;
  return [0, 2, 4]
    .map((i) => {
      const angle = (Math.PI / 3) * i;
      const x = Math.round(Math.cos(angle) * inner * 100) / 100;
      const y = Math.round(Math.sin(angle) * inner * 100) / 100;
      return `M0 0L${x} ${y}`;
    })
    .join("");
}
