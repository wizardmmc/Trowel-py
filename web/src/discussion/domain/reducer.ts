/** 以版本和事件序号折叠可重连的研讨公开快照。 */

import type { Discussion, DiscussionEvent } from "./types";

export interface DiscussionDomainState {
  readonly discussion: Discussion | null;
  readonly lastSequence: number;
}

export type DiscussionDomainAction =
  | { readonly type: "snapshot"; readonly discussion: Discussion }
  | { readonly type: "event"; readonly event: DiscussionEvent }
  | { readonly type: "clear" };

export const INITIAL_DISCUSSION_DOMAIN_STATE: DiscussionDomainState = {
  discussion: null,
  lastSequence: 0,
};

/** 丢弃旧快照和重复事件，不从事件到达顺序推断未公开正文。 */
export function reduceDiscussion(
  state: DiscussionDomainState,
  action: DiscussionDomainAction,
): DiscussionDomainState {
  if (action.type === "clear") return INITIAL_DISCUSSION_DOMAIN_STATE;
  if (action.type === "event") {
    if (action.event.sequence <= state.lastSequence) return state;
    return { ...state, lastSequence: action.event.sequence };
  }
  if (
    state.discussion &&
    action.discussion.id === state.discussion.id &&
    action.discussion.version < state.discussion.version
  ) {
    return state;
  }
  return { ...state, discussion: action.discussion };
}
