import type { PetMood } from "../../api/client";

interface PetSVGProps {
  readonly mood: PetMood;
  readonly equippedHat?: string;
}

const ORANGE = "#E89B4C";
const ORANGE_DARK = "#C9772B";
const CREAM = "#FBF3E4";
const OUTLINE = "#3D2817";
const EYE = "#2A1A0F";
const BLUSH = "#F4A8A0";
const GOLD = "#E8B84B";
const GREEN = "#5B8C3E";

function BodyParts() {
  return (
    <>
      <path d="M13 17 L17 4 L23 16 Z" fill={ORANGE} stroke={OUTLINE} strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M16 14 L17.6 8 L20.6 13 Z" fill={ORANGE_DARK} />
      <path d="M25 16 L31 4 L35 17 Z" fill={ORANGE} stroke={OUTLINE} strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M27.4 13 L30.4 8 L32 14 Z" fill={ORANGE_DARK} />
      <circle cx="24" cy="27" r="15" fill={ORANGE} stroke={OUTLINE} strokeWidth="2" />
      <ellipse cx="24" cy="34" rx="10" ry="6.5" fill={CREAM} />
      <circle cx="14" cy="30" r="2.2" fill={BLUSH} opacity="0.55" />
      <circle cx="34" cy="30" r="2.2" fill={BLUSH} opacity="0.55" />
      <ellipse cx="24" cy="30" rx="1.8" ry="1.3" fill={EYE} />
    </>
  );
}

function Eyes({ mood }: { mood: PetMood }) {
  switch (mood) {
    case "excited":
      return (
        <>
          <circle cx="19" cy="25" r="2.4" fill={EYE} />
          <circle cx="19.8" cy="24.2" r="0.9" fill={CREAM} />
          <circle cx="29" cy="25" r="2.4" fill={EYE} />
          <circle cx="29.8" cy="24.2" r="0.9" fill={CREAM} />
        </>
      );
    case "happy":
      return (
        <>
          <path d="M17 26 Q19 22.5 21 26" stroke={EYE} strokeWidth="1.7" fill="none" strokeLinecap="round" />
          <path d="M27 26 Q29 22.5 31 26" stroke={EYE} strokeWidth="1.7" fill="none" strokeLinecap="round" />
        </>
      );
    case "curious":
      return (
        <>
          <circle cx="19" cy="25" r="2.4" fill={EYE} />
          <circle cx="19.8" cy="24.2" r="0.9" fill={CREAM} />
          <path d="M27 25 Q29 22.5 31 25" stroke={EYE} strokeWidth="1.7" fill="none" strokeLinecap="round" />
        </>
      );
    case "normal":
    default:
      return (
        <>
          <circle cx="19" cy="25" r="1.7" fill={EYE} />
          <circle cx="29" cy="25" r="1.7" fill={EYE} />
        </>
      );
  }
}

function Mouth({ mood }: { mood: PetMood }) {
  switch (mood) {
    case "excited":
      return (
        <>
          <ellipse cx="24" cy="35" rx="2.4" ry="1.8" fill={EYE} />
          <ellipse cx="24" cy="36.2" rx="1.3" ry="0.8" fill="#C45B4A" />
        </>
      );
    case "happy":
      return <path d="M20 33 Q24 37 28 33" stroke={EYE} strokeWidth="1.7" fill="none" strokeLinecap="round" />;
    case "curious":
      return <path d="M21 34 Q23 35.5 24 33 Q26 35.5 27 34" stroke={EYE} strokeWidth="1.6" fill="none" strokeLinecap="round" />;
    case "normal":
    default:
      return <path d="M22 34 Q24 35.3 26 34" stroke={EYE} strokeWidth="1.5" fill="none" strokeLinecap="round" />;
  }
}

function Hat({ id }: { id: string }) {
  switch (id) {
    case "hat_scholar":
      return (
        <>
          <path d="M8 13 L24 6 L40 13 L24 19 Z" fill="#1F1A14" stroke={OUTLINE} strokeWidth="1.2" strokeLinejoin="round" />
          <path d="M24 6 L24 2 L33 4 L33 9" stroke={GOLD} strokeWidth="1.4" fill="none" strokeLinecap="round" />
          <circle cx="33" cy="10" r="1.4" fill={GOLD} />
        </>
      );
    case "hat_wreath":
      return (
        <>
          <ellipse cx="24" cy="11" rx="14" ry="4.5" fill="none" stroke={GREEN} strokeWidth="2" />
          <circle cx="14" cy="10" r="1.6" fill="#FF8FA3" />
          <circle cx="24" cy="7.5" r="1.6" fill={GOLD} />
          <circle cx="34" cy="10" r="1.6" fill="#FF8FA3" />
          <circle cx="19" cy="13" r="1.1" fill="#FFE66D" />
          <circle cx="29" cy="13" r="1.1" fill="#FFE66D" />
        </>
      );
    case "hat_straw":
    default:
      return (
        <>
          <ellipse cx="24" cy="14" rx="15" ry="3.5" fill="#C9772B" stroke={OUTLINE} strokeWidth="1.2" />
          <ellipse cx="24" cy="9" rx="9" ry="6" fill="#E8B86C" stroke={OUTLINE} strokeWidth="1.2" />
          <path d="M15 13 Q24 15 33 13" stroke={GOLD} strokeWidth="1.6" fill="none" />
        </>
      );
  }
}

export function PetSVG({ mood, equippedHat }: PetSVGProps) {
  return (
    <svg data-testid="pet-svg" viewBox="0 0 48 48" width="100%" height="100%">
      <BodyParts />
      <g data-mood={mood}>
        <Eyes mood={mood} />
        <Mouth mood={mood} />
      </g>
      {equippedHat && (
        <g data-hat={equippedHat}>
          <Hat id={equippedHat} />
        </g>
      )}
    </svg>
  );
}
