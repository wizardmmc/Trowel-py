import type { RefObject } from "react";
import type {
  EventLog,
  InventoryItem,
  Pet,
  PetResponse,
  PlayerProfile,
} from "../../api/client";
import { PetSVG } from "./PetSVG";
import {
  EVENT_TYPE_LABELS,
  FOOD_ITEMS,
  ITEM_CATALOG,
  MOOD_LABELS,
} from "./itemCatalog";

function formatTime(iso: string): string {
  const date = new Date(iso);
  const month = date.getMonth() + 1;
  const day = date.getDate();
  const hour = date.getHours().toString().padStart(2, "0");
  const minute = date.getMinutes().toString().padStart(2, "0");
  return `${month}/${day} ${hour}:${minute}`;
}

interface PetPanelContentProps {
  readonly pet: Pet | null;
  readonly lastResponse: PetResponse | null;
  readonly player: PlayerProfile | null;
  readonly inventory: readonly InventoryItem[];
  readonly events: readonly EventLog[];
  readonly actionError: string | null;
  readonly isLoading: boolean;
  readonly closeRef: RefObject<HTMLButtonElement | null>;
  readonly onClose: () => void;
  readonly onFeed: (catalogId: string) => void;
  readonly onEquipHat: (rowId: string) => void;
}

export function PetPanelContent({
  pet,
  lastResponse,
  player,
  inventory,
  events,
  actionError,
  isLoading,
  closeRef,
  onClose,
  onFeed,
  onEquipHat,
}: PetPanelContentProps) {
  const equippedRow = pet?.equipped_hat
    ? inventory.find((item) => item.id === pet.equipped_hat)
    : null;
  const equippedCatalogId = equippedRow?.item_id;
  const equippedCatalog = equippedCatalogId
    ? ITEM_CATALOG[equippedCatalogId]
    : null;
  const moodInfo = pet
    ? MOOD_LABELS[pet.mood] ?? MOOD_LABELS.normal
    : MOOD_LABELS.normal;
  const hatItems = inventory.filter((item) => item.item_type === "hat");

  return (
    <>
      <div className="pet-panel__header">
        <h2 className="pet-panel__title">小锤的花园小屋</h2>
        <button
          ref={closeRef}
          className="pet-panel__close"
          onClick={onClose}
          aria-label="关闭面板"
        >
          {"✕"}
        </button>
      </div>

      <div className="pet-panel__pet-area">
        <div className="pet-panel__pet-svg">
          <PetSVG
            mood={pet?.mood ?? "normal"}
            equippedHat={equippedCatalogId}
          />
        </div>
        {lastResponse && (
          <div className="pet-panel__speech-bubble">{lastResponse.text}</div>
        )}
      </div>

      <div className="pet-panel__section">
        <h3 className="pet-panel__section-title">状态</h3>
        <div className="pet-panel__status-list">
          <div className="pet-panel__status-item">
            <span className="pet-panel__status-label">心情</span>
            <span>{`${moodInfo.emoji} ${moodInfo.text}`}</span>
          </div>
          <div className="pet-panel__status-item">
            <span className="pet-panel__status-label">饱腹度</span>
            <div className="pet-panel__hunger-bar">
              <div
                className="pet-panel__hunger-fill"
                style={{ width: `${pet?.hunger ?? 0}%` }}
              />
            </div>
            <span className="pet-panel__hunger-value">{`${pet?.hunger ?? 0}%`}</span>
          </div>
          <div className="pet-panel__status-item">
            <span className="pet-panel__status-label">装备</span>
            <span>
              {equippedCatalog
                ? `${equippedCatalog.emoji} ${equippedCatalog.label}`
                : "无"}
            </span>
          </div>
        </div>
      </div>

      <div className="pet-panel__section">
        <h3 className="pet-panel__section-title">喂食</h3>
        <div className="pet-panel__food-list">
          {FOOD_ITEMS.map(([itemId, entry]) => {
            const ownedCount = inventory.filter(
              (item) => item.item_id === itemId && item.item_type === "food",
            ).length;
            const canAfford = (player?.coins ?? 0) >= entry.price;
            const canFeed = ownedCount > 0 || canAfford;
            return (
              <button
                key={itemId}
                className="pet-panel__food-btn"
                disabled={isLoading || !canFeed}
                onClick={() => onFeed(itemId)}
              >
                <span className="pet-panel__food-info">
                  <span>{entry.emoji}</span>
                  <span>{entry.label}</span>
                  {ownedCount > 0 && (
                    <span className="pet-panel__food-owned">{`x${ownedCount}`}</span>
                  )}
                </span>
                <span className="pet-panel__food-price">
                  {ownedCount > 0 ? "背包" : `${entry.price} 🪙`}
                </span>
              </button>
            );
          })}
        </div>
        {actionError && <div className="pet-panel__error">{actionError}</div>}
      </div>

      <div className="pet-panel__section">
        <h3 className="pet-panel__section-title">背包 - 帽子</h3>
        {hatItems.length === 0 ? (
          <div className="pet-panel__empty">还没有帽子，继续探索吧！</div>
        ) : (
          <div className="pet-panel__hat-list">
            {hatItems.map((item) => {
              const catalog = ITEM_CATALOG[item.item_id];
              const isEquipped = pet?.equipped_hat === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  className={`pet-panel__hat-item${isEquipped ? " pet-panel__hat-item--equipped" : ""}`}
                  onClick={() => onEquipHat(item.id)}
                >
                  <span className="pet-panel__hat-info">
                    <span>{catalog?.emoji ?? "?"}</span>
                    <span>{catalog?.label ?? item.item_id}</span>
                  </span>
                  {isEquipped && <span className="pet-panel__hat-check">✓</span>}
                </button>
              );
            })}
          </div>
        )}
      </div>

      <div className="pet-panel__section">
        <h3 className="pet-panel__section-title">最近事件</h3>
        {events.length === 0 ? (
          <div className="pet-panel__empty">暂无事件记录</div>
        ) : (
          <div className="pet-panel__event-list">
            {events.map((event) => (
              <div key={event.id} className="pet-panel__event-item">
                <span>
                  <span className="pet-panel__event-type">
                    {EVENT_TYPE_LABELS[event.event_type] ?? event.event_type}
                  </span>
                  <span className="pet-panel__event-time">
                    {formatTime(event.triggered_at)}
                  </span>
                </span>
                <span className="pet-panel__event-reward">
                  {event.reward_xp > 0 && `+${event.reward_xp}XP`}
                  {event.reward_coin > 0 && ` +${event.reward_coin}🪙`}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
