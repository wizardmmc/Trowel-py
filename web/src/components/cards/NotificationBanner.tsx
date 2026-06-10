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
        {count} new card{count > 1 ? "s" : ""} pending review
      </span>
      <span className="notification-banner__action">Review now &rarr;</span>
    </div>
  );
}
