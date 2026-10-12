import { he } from '../i18n/he';
import { Icon } from './Icon';
import { Logo } from './Logo';
import { useTheme } from '../theme/theme';
import './TopBar.css';

export function TopBar() {
  const [theme, setTheme] = useTheme();
  const dark = theme === 'dark';
  const label = dark ? he.theme.toLight : he.theme.toDark;
  return (
    <header className="topbar">
      <div className="topbar__brand">
        <Logo />
        <h1 className="topbar__name">
          {he.app.name} <span className="topbar__accent">{he.app.nameAccent}</span>
        </h1>
        <span className="topbar__project">{he.app.project}</span>
      </div>
      <div className="topbar__end">
        <button
          type="button"
          className="topbar__theme"
          aria-label={label}
          title={label}
          onClick={() => setTheme(dark ? 'light' : 'dark')}
        >
          <Icon name={dark ? 'sun' : 'moon'} size={18} />
        </button>
        {/* No account features exist yet, so this is a label, not a button. */}
        <div className="topbar__avatar" role="img" aria-label={he.account.aria}>
          <Icon name="user" size={18} />
        </div>
      </div>
    </header>
  );
}
