/**
 * Deterministic state machine for the session lifecycle.
 *
 * States
 * ──────
 *  inactive   – no session is active
 *  running    – session active, tab visible, user potentially active
 *  hidden     – active tab is hidden (tab was backgrounded inside Chrome)
 *  background – Chrome window lost OS focus entirely
 *  idle       – session running but no user activity for IDLE_THRESHOLD
 *  finished   – session ended (terminal; no further transitions)
 *
 * Transitions are whitelist-based: any unlisted move is rejected with a
 * console warning.  Callers MUST check the return value.
 */
import { SESSION_STATES } from '../shared/constants.js';

const ALLOWED_TRANSITIONS = Object.freeze({
  [SESSION_STATES.INACTIVE]: [
    SESSION_STATES.RUNNING,
  ],
  [SESSION_STATES.RUNNING]: [
    SESSION_STATES.HIDDEN,
    SESSION_STATES.BACKGROUND,
    SESSION_STATES.IDLE,
    SESSION_STATES.FINISHED,
  ],
  [SESSION_STATES.HIDDEN]: [
    SESSION_STATES.RUNNING,
    SESSION_STATES.BACKGROUND,
    SESSION_STATES.FINISHED,
  ],
  [SESSION_STATES.BACKGROUND]: [
    SESSION_STATES.RUNNING,
    SESSION_STATES.HIDDEN,
    SESSION_STATES.FINISHED,
  ],
  [SESSION_STATES.IDLE]: [
    SESSION_STATES.RUNNING,
    SESSION_STATES.HIDDEN,
    SESSION_STATES.BACKGROUND,
    SESSION_STATES.FINISHED,
  ],
  [SESSION_STATES.FINISHED]: [],
});

export class StateMachine {
  constructor(initialState = SESSION_STATES.INACTIVE) {
    this._state    = initialState;
    this._listeners = [];
  }

  get state() {
    return this._state;
  }

  /** Force-set state (used only when restoring persisted session). */
  set state(s) {
    this._state = s;
  }

  /**
   * Attempt a state transition.
   * @returns {boolean} true if the transition was accepted.
   */
  transition(nextState) {
    const allowed = ALLOWED_TRANSITIONS[this._state] ?? [];

    if (!allowed.includes(nextState)) {
      console.warn(
        `[StateMachine] Rejected transition ${this._state} → ${nextState}`,
      );
      return false;
    }

    const prev  = this._state;
    this._state = nextState;
    for (const fn of this._listeners) fn(prev, nextState);
    return true;
  }

  is(state)    { return this._state === state; }
  isInactive() { return this._state === SESSION_STATES.INACTIVE;  }
  isFinished() { return this._state === SESSION_STATES.FINISHED;  }

  /**
   * True while any non-terminal, non-inactive state is active.
   * Used to gate all event collection logic.
   */
  isActive() {
    return (
      this._state === SESSION_STATES.RUNNING    ||
      this._state === SESSION_STATES.HIDDEN     ||
      this._state === SESSION_STATES.BACKGROUND ||
      this._state === SESSION_STATES.IDLE
    );
  }

  /** Register a listener called on every accepted transition. */
  onTransition(fn) {
    this._listeners.push(fn);
  }
}
