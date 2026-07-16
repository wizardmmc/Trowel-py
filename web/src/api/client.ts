const API_BASE = "/api/cards";
const REVIEW_API_BASE = "/api/review";
const GARDEN_API_BASE = "/api/garden";

export interface CardDraft {
  id: string;
  title: string;
  category: string;
  explanation: string;
  example: string | null;
  difficulty: number;
  tags: string[];
  confidence: number;
  source_type: string;
  source: string | null;
}

export interface Card {
  id: string;
  title: string;
  category: string;
  explanation: string;
  example: string | null;
  difficulty: number;
  source: string | null;
  tags: string[];
  status: string;
  created_at: string;
  updated_at: string;
}

interface ApiResponse<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(`API error: ${response.status}`);
  }
  const result: ApiResponse<T> = await response.json();
  if (!result.success || result.error) {
    throw new Error(result.error ?? "Unknown error");
  }
  return result.data as T;
}

export async function extractCards(content: string): Promise<{ drafts: CardDraft[] }> {
  return request<{ drafts: CardDraft[] }>(`${API_BASE}/extract`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
}

/** POST /extract-conversation — extract cards from a CC JSONL conversation log.
 *  Backend parses the raw text (handles real CC format). */
export async function extractConversation(
  content: string,
): Promise<{ drafts: CardDraft[] }> {
  return request<{ drafts: CardDraft[] }>(`${API_BASE}/extract-conversation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
}

export async function reviewCard(
  draftId: string,
  action: "accept" | "edit" | "reject",
  edits?: Record<string, unknown>
): Promise<{ card?: Card; rejected?: boolean }> {
  return request<{ card?: Card; rejected?: boolean }>(
    `${API_BASE}/${draftId}/review`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, edits }),
    }
  );
}

export async function findDuplicates(
  draftId: string
): Promise<{ duplicates: Card[] }> {
  return request<{ duplicates: Card[] }>(`${API_BASE}/${draftId}/dedup`);
}

/**
 * POST /re-explain — regenerate a draft's explanation from a different angle
 * (slice 021). Stateless generator: no DB writes. The caller keeps candidate
 * versions in state and writes the chosen one back via reviewCard — accept for
 * the original, edit+{explanation} for a regenerated version.
 *
 * user_hint is optional; omitted/null means "regenerate freely".
 */
export async function reExplain(
  explanation: string,
  title: string,
  category: string,
  userHint?: string,
): Promise<{ explanation: string }> {
  return request<{ explanation: string }>(`${API_BASE}/re-explain`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ explanation, title, category, user_hint: userHint }),
  });
}

export async function getAllCards(
  page = 1,
  limit = 20
): Promise<{ cards: Card[]; total: number; page: number; limit: number }> {
  return request(`${API_BASE}?page=${page}&limit=${limit}`);
}

// ── Review API types & functions ──

export interface FSRSState {
  card_id: string;
  stability: number;
  difficulty: number;
  elapsed_days: number;
  scheduled_days: number;
  reps: number;
  lapses: number;
  state: 0 | 1 | 2 | 3;
  due: string;
  last_review: string | null;
}

export interface DueCard {
  card: Card;
  fsrs_state: FSRSState;
  plant_stage: string;
}

export interface SubmitResult {
  card: Card;
  fsrs_state: FSRSState;
  review_log: {
    id: string;
    card_id: string;
    rating: number;
    state: number;
    elapsed_days: number;
    scheduled_days: number;
    duration_ms: number | null;
    created_at: string;
  };
  plant_stage: string;
  plant_changed: boolean;
}

export interface SessionStats {
  total: number;
  avg_rating: number;
  accuracy: number;
}

export async function getDueCards(): Promise<DueCard[]> {
  return request<DueCard[]>(`${REVIEW_API_BASE}/due`);
}

export async function submitReview(
  cardId: string,
  rating: number,
): Promise<SubmitResult> {
  return request<SubmitResult>(`${REVIEW_API_BASE}/submit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ card_id: cardId, rating }),
  });
}

export async function getSessionStats(since: string): Promise<SessionStats> {
  return request<SessionStats>(
    `${REVIEW_API_BASE}/session-stats?since=${encodeURIComponent(since)}`,
  );
}

// ── Garden API types & functions ──

export interface GardenPlant {
  card_id: string;
  title: string;
  category: string;
  explanation: string;
  plant_stage: "seed" | "sprout" | "tree" | "wilting";
  fsrs_state: number | null;
  due: string | null;
  reps: number;
}

export interface GardenStatsData {
  total_plants: number;
  due_count: number;
  flowering_rate: number;
}

export async function getGardenPlants(): Promise<GardenPlant[]> {
  return request<GardenPlant[]>(`${GARDEN_API_BASE}/plants`);
}

export async function getGardenStats(): Promise<GardenStatsData> {
  return request<GardenStatsData>(`${GARDEN_API_BASE}/stats`);
}

export async function searchCards(query: string): Promise<GardenPlant[]> {
  return request<GardenPlant[]>(
    `${API_BASE}/search?q=${encodeURIComponent(query)}`,
  );
}

// ── Pet API types & functions ──

const PET_API_BASE = "/api/pet";

export type PetMood = "happy" | "excited" | "curious" | "normal";

export interface Pet {
  /** always 'default' in the single-user system */
  readonly player_id: string;
  readonly mood: PetMood;
  /** satiety 0-100 */
  readonly hunger: number;
  /** inventory row id of the worn hat, or null when bare-headed */
  readonly equipped_hat: string | null;
  readonly updated_at: string;
}

export interface PetResponse {
  readonly text: string;
  readonly mood: PetMood;
}

export interface InteractResult {
  readonly response: PetResponse;
  readonly pet: Pet;
}

/** GET /api/pet — the pet's current state */
export async function fetchPet(): Promise<Pet> {
  return request<Pet>(PET_API_BASE);
}

/** POST /api/pet/feed — eat one food item, returns the updated pet */
export async function feedPet(itemId: string): Promise<Pet> {
  return request<Pet>(`${PET_API_BASE}/feed`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_id: itemId }),
  });
}

/** POST /api/pet/interact — pet the pet, returns a line + the updated pet */
export async function interactPet(): Promise<InteractResult> {
  return request<InteractResult>(`${PET_API_BASE}/interact`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
}

/** PUT /api/pet/equip — wear a hat, returns the updated pet */
export async function equipHat(itemId: string): Promise<Pet> {
  return request<Pet>(`${PET_API_BASE}/equip`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_id: itemId }),
  });
}

// ── Player API types & functions ──

const PLAYER_API_BASE = "/api/player";

export interface PlayerProfile {
  readonly id: string;
  readonly xp: number;
  /** spendable currency; buying food/hats deducts from this */
  readonly coins: number;
  readonly streak_days: number;
  readonly last_active: string;
  readonly created_at: string;
  /** derived from xp by the backend (level n needs n*(n-1)*50 xp) */
  readonly level: number;
  readonly xp_to_next_level: number;
}

export interface InventoryItem {
  /** inventory row id (uuid); this is what feed/equip expect, NOT item_id */
  readonly id: string;
  readonly player_id: string;
  /** catalog id, e.g. food_basic / hat_straw */
  readonly item_id: string;
  readonly item_type: "food" | "hat";
  /** 0 or 1 — whether a hat is currently worn */
  readonly equipped: number;
  readonly obtained_at: string;
}

export interface BuyResult {
  readonly item_id: string;
  readonly item_type: "food" | "hat";
}

/** GET /api/player — profile with computed level fields */
export async function fetchPlayer(): Promise<PlayerProfile> {
  return request<PlayerProfile>(PLAYER_API_BASE);
}

/** GET /api/player/inventory — every owned item (food + hats) */
export async function fetchInventory(): Promise<InventoryItem[]> {
  return request<InventoryItem[]>(`${PLAYER_API_BASE}/inventory`);
}

/**
 * POST /api/player/buy — spend coins, grant one item.
 * Returns only the catalog id + type (not the new row id), so callers must
 * re-fetch the inventory to resolve the granted row.
 */
export async function buyItem(itemId: string): Promise<BuyResult> {
  return request<BuyResult>(`${PLAYER_API_BASE}/buy`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_id: itemId }),
  });
}

// ── Profile API types & functions ──

const PROFILE_API_BASE = "/api/profile";

/** file-level provenance stamp: user-edit (hand edit, default) or ai-calibration
 * (the front-end passes this when merging accepted AI suggestions — slice-050). */
export type ProfileSource = "user-edit" | "ai-calibration";

/** PUT /api/profile body: the five editable dimensions. `updated` is always
 * server-stamped. `source` is optional (defaults to user-edit; pass
 * ai-calibration on the accept-merge path). Mirrors the five-dim Profile
 * dataclass (slice-047); `other` is always present. */
export interface ProfileUpdate {
  readonly ability: string;
  readonly methodology: string;
  readonly expression: string;
  readonly goal: string;
  readonly other: string;
  readonly source?: ProfileSource;
}

/** GET/PUT /api/profile response: five dims + provenance. */
export interface ProfileDTO {
  readonly ability: string;
  readonly methodology: string;
  readonly expression: string;
  readonly goal: string;
  readonly other: string;
  readonly updated: string;
  readonly source: string;
}

/** GET /api/profile — the user self-description profile (empty dims on cold start). */
export async function fetchProfile(): Promise<ProfileDTO> {
  return request<ProfileDTO>(PROFILE_API_BASE);
}

/** PUT /api/profile — write the five dims back to profile.md via the store.
 * Returns the freshly loaded profile (server-stamped updated/source), so the
 * caller need not re-GET. */
export async function putProfile(input: ProfileUpdate): Promise<ProfileDTO> {
  return request<ProfileDTO>(PROFILE_API_BASE, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

// ── Profile suggestions (slice-050) ──

/** which of the five profile dims a suggestion targets (mirrors ProfileUpdate). */
export type ProfileDimension =
  | "ability"
  | "methodology"
  | "expression"
  | "goal"
  | "other";

/** lifecycle of a suggestion in the candidate queue. */
export type SuggestionStatus = "pending" | "accepted" | "discarded";

/** GET /api/profile/suggestions item: one AI-proposed profile addition (a
 * pending candidate the user accepts — merged into profile via PUT — or
 * discards). The agent never writes profile.md (C-1). */
export interface Suggestion {
  readonly id: string;
  readonly dimension: ProfileDimension;
  readonly body: string;
  readonly sources: readonly string[];
  readonly date: string;
  readonly status: SuggestionStatus;
}

/** GET /api/profile/suggestions — the pending AI suggestions for the user. */
export async function getSuggestions(): Promise<Suggestion[]> {
  return request<Suggestion[]>(`${PROFILE_API_BASE}/suggestions`);
}

/** PATCH /api/profile/suggestions/{id} — accept / discard one suggestion.
 * Accept does NOT write profile.md here; the caller merges the accepted body
 * into the profile and PUTs with source=ai-calibration. */
export async function patchSuggestionStatus(
  id: string,
  status: "accepted" | "discarded",
): Promise<void> {
  await request<null>(`${PROFILE_API_BASE}/suggestions/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
}

// ── Events API types & functions ──

const EVENTS_API_BASE = "/api/events";

export interface EventLog {
  readonly id: string;
  readonly player_id: string;
  readonly event_type: string;
  readonly reward_xp: number;
  readonly reward_coin: number;
  readonly reward_item_id: string | null;
  readonly description: string | null;
  readonly card_id: string | null;
  readonly triggered_at: string;
}

/** GET /api/events/history — most recent event logs, newest first */
export async function fetchEventHistory(limit: number): Promise<EventLog[]> {
  return request<EventLog[]>(`${EVENTS_API_BASE}/history?limit=${limit}`);
}

/**
 * POST /api/events/trigger — run one event cycle synchronously.
 * Returns the event log when something fired, or null when nothing was
 * eligible (cooldown / no candidate). This is a plain request/response, not
 * SSE — the py backend has no server-push source, so a synchronous fetch is
 * the right shape here (see docs/training-log-m2.md slice 016 rationale).
 */
export async function triggerEvent(): Promise<EventLog | null> {
  return request<EventLog | null>(`${EVENTS_API_BASE}/trigger`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
}

// ── Feynman API types & functions ──

const FEYNMAN_API_BASE = "/api/feynman";

/** Result from POST /generate — a fresh drill question + its session id */
export interface FeynmanQuestion {
  readonly session_id: string;
  readonly question: string;
  /** null when the LLM offers no hint */
  readonly hint: string | null;
}

/** Result from POST /evaluate — the LLM's scores for a user's answer */
export interface FeynmanEvaluation {
  readonly session_id: string;
  readonly accuracy: number;
  readonly completeness: number;
  readonly feedback: string;
  readonly missed_points: readonly string[];
}

/** One row of GET /history — a past (possibly unevaluated) session */
export interface FeynmanHistoryItem {
  readonly id: string;
  readonly card_id: string;
  readonly question: string;
  readonly user_answer: string | null;
  readonly accuracy: number | null;
  readonly completeness: number | null;
  readonly feedback: string | null;
  readonly missed_points: readonly string[] | null;
  readonly created_at: string | null;
}

/** POST /api/feynman/generate — ask the LLM for a drill question on a card */
export async function generateFeynmanQuestion(
  cardId: string,
): Promise<FeynmanQuestion> {
  return request<FeynmanQuestion>(`${FEYNMAN_API_BASE}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ card_id: cardId }),
  });
}

/** POST /api/feynman/evaluate — grade a user's answer against the card */
export async function evaluateFeynmanAnswer(
  sessionId: string,
  answer: string,
): Promise<FeynmanEvaluation> {
  return request<FeynmanEvaluation>(`${FEYNMAN_API_BASE}/evaluate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, answer }),
  });
}

/** GET /api/feynman/history/{cardId} — past sessions for a card, newest first */
export async function getFeynmanHistory(
  cardId: string,
): Promise<FeynmanHistoryItem[]> {
  return request<FeynmanHistoryItem[]>(
    `${FEYNMAN_API_BASE}/history/${encodeURIComponent(cardId)}`,
  );
}
