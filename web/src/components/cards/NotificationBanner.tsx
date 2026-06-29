interface NotificationBannerProps {
  count: number;
  onClick: () => void;
}

export function NotificationBanner({ count, onClick }: NotificationBannerProps) {
  if (count === 0) return null;

  return (
    <div
      className="notification-banner"
      onClick={onClick}
      data-testid="notification-banner"
      role="button"
    >
      <span className="notification-banner__text">
        有 {count} 张卡片待审核
      </span>
      <span className="notification-banner__action">去审核 &rarr;</span>
    </div>
  );
}
