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


const PET_API_BASE = "/api/pet";

export type PetMood = "happy" | "excited" | "curious" | "normal";

export interface Pet {
  readonly player_id: string;
  readonly mood: PetMood;
  readonly hunger: number;
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

export async function fetchPet(): Promise<Pet> {
  return request<Pet>(PET_API_BASE);
}

export async function feedPet(itemId: string): Promise<Pet> {
  return request<Pet>(`${PET_API_BASE}/feed`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_id: itemId }),
  });
}

export async function interactPet(): Promise<InteractResult> {
  return request<InteractResult>(`${PET_API_BASE}/interact`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
}

export async function equipHat(itemId: string): Promise<Pet> {
  return request<Pet>(`${PET_API_BASE}/equip`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_id: itemId }),
  });
}


const PLAYER_API_BASE = "/api/player";

export interface PlayerProfile {
  readonly id: string;
  readonly xp: number;
  readonly coins: number;
  readonly streak_days: number;
  readonly last_active: string;
  readonly created_at: string;
  readonly level: number;
  readonly xp_to_next_level: number;
}

export interface InventoryItem {
  /** inventory 行 ID；feed/equip 接收它，而不是 item_id。 */
  readonly id: string;
  readonly player_id: string;
  readonly item_id: string;
  readonly item_type: "food" | "hat";
  readonly equipped: number;
  readonly obtained_at: string;
}

export interface BuyResult {
  readonly item_id: string;
  readonly item_type: "food" | "hat";
}

export async function fetchPlayer(): Promise<PlayerProfile> {
  return request<PlayerProfile>(PLAYER_API_BASE);
}

export async function fetchInventory(): Promise<InventoryItem[]> {
  return request<InventoryItem[]>(`${PLAYER_API_BASE}/inventory`);
}

/** 购买响应不含 inventory 行 ID，调用方需重新读取背包。 */
export async function buyItem(itemId: string): Promise<BuyResult> {
  return request<BuyResult>(`${PLAYER_API_BASE}/buy`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_id: itemId }),
  });
}


const PROFILE_API_BASE = "/api/profile";

export type ProfileSource = "user-edit" | "ai-calibration";

export interface ProfileUpdate {
  readonly ability: string;
  readonly methodology: string;
  readonly expression: string;
  readonly goal: string;
  readonly other: string;
  readonly source?: ProfileSource;
}

export interface ProfileDTO {
  readonly ability: string;
  readonly methodology: string;
  readonly expression: string;
  readonly goal: string;
  readonly other: string;
  readonly updated: string;
  readonly source: string;
}

export async function fetchProfile(): Promise<ProfileDTO> {
  return request<ProfileDTO>(PROFILE_API_BASE);
}

export async function putProfile(input: ProfileUpdate): Promise<ProfileDTO> {
  return request<ProfileDTO>(PROFILE_API_BASE, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}


export type ProfileDimension =
  | "ability"
  | "methodology"
  | "expression"
  | "goal"
  | "other";

export type SuggestionStatus = "pending" | "accepted" | "discarded";

export interface Suggestion {
  readonly id: string;
  readonly dimension: ProfileDimension;
  readonly body: string;
  readonly sources: readonly string[];
  readonly date: string;
  readonly status: SuggestionStatus;
}

export async function getSuggestions(): Promise<Suggestion[]> {
  return request<Suggestion[]>(`${PROFILE_API_BASE}/suggestions`);
}

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

export async function fetchEventHistory(limit: number): Promise<EventLog[]> {
  return request<EventLog[]>(`${EVENTS_API_BASE}/history?limit=${limit}`);
}

/** 事件引擎没有推送源，因此这里同步触发并返回本轮结果。 */
export async function triggerEvent(): Promise<EventLog | null> {
  return request<EventLog | null>(`${EVENTS_API_BASE}/trigger`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
}


const FEYNMAN_API_BASE = "/api/feynman";

export interface FeynmanQuestion {
  readonly session_id: string;
  readonly question: string;
  readonly hint: string | null;
}

export interface FeynmanEvaluation {
  readonly session_id: string;
  readonly accuracy: number;
  readonly completeness: number;
  readonly feedback: string;
  readonly missed_points: readonly string[];
}

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

export async function generateFeynmanQuestion(
  cardId: string,
): Promise<FeynmanQuestion> {
  return request<FeynmanQuestion>(`${FEYNMAN_API_BASE}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ card_id: cardId }),
  });
}

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

export async function getFeynmanHistory(
  cardId: string,
): Promise<FeynmanHistoryItem[]> {
  return request<FeynmanHistoryItem[]>(
    `${FEYNMAN_API_BASE}/history/${encodeURIComponent(cardId)}`,
  );
}
