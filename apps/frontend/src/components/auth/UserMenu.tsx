import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/stores/auth-store';

export function UserMenu() {
  const user = useAuthStore((s) => s.user);
  const signOut = useAuthStore((s) => s.signOut);
  const navigate = useNavigate();
  if (!user) return null;

  return (
    <div className="user-menu">
      {user.avatar_url && <img className="user-menu__avatar" src={user.avatar_url} alt="" referrerPolicy="no-referrer" />}
      <span className="user-menu__name" title={user.email}>
        {user.name}
        {user.role === 'admin' && <span className="user-menu__role">admin</span>}
      </span>
      <button
        className="btn btn--ghost btn--small"
        onClick={async () => {
          await signOut();
          navigate('/login', { replace: true });
        }}
      >
        Sign out
      </button>
    </div>
  );
}
