const API_BASE = "http://localhost:8000/api/cards";
const REVIEW_API_BASE = "http://localhost:8000/api/review";
const GARDEN_API_BASE = "http://localhost:8000/api/garden";

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

const PET_API_BASE = "http://localhost:8000/api/pet";

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
