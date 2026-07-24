import type { PetMood } from "../../api/client";

export interface CatalogEntry {
  readonly label: string;
  readonly emoji: string;
  readonly price: number;
  readonly type: "food" | "hat";
}

export const ITEM_CATALOG: Record<string, CatalogEntry> = {
  food_basic: { label: "基础食物", emoji: "🍙", price: 10, type: "food" },
  food_premium: { label: "高级食物", emoji: "🍱", price: 25, type: "food" },
  hat_straw: { label: "小草帽", emoji: "👒", price: 50, type: "hat" },
  hat_scholar: { label: "学者帽", emoji: "🎓", price: 100, type: "hat" },
  hat_wreath: { label: "花环", emoji: "💐", price: 75, type: "hat" },
};

export const FOOD_ITEMS = Object.entries(ITEM_CATALOG).filter(
  ([, entry]) => entry.type === "food",
);

export const MOOD_LABELS: Record<PetMood, { emoji: string; text: string }> = {
  happy: { emoji: "😊", text: "开心" },
  excited: { emoji: "🎉", text: "兴奋" },
  curious: { emoji: "🤔", text: "好奇" },
  normal: { emoji: "😐", text: "平静" },
};

export const EVENT_TYPE_LABELS: Record<string, string> = {
  sign_in: "签到",
  challenge: "挑战",
  discovery: "发现",
  story: "故事",
  growth: "成长",
  gift: "礼物",
  feynman: "费曼",
};

export const EVENT_ICONS: Record<string, string> = {
  sign_in: "🌅",
  challenge: "🎯",
  discovery: "✨",
  story: "📖",
  growth: "🌱",
  gift: "🎁",
  feynman: "💡",
};
