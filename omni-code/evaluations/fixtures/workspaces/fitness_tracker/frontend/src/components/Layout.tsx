import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  LayoutDashboard,
  Dumbbell,
  Library,
  Activity,
} from 'lucide-react';

const navItems = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/workouts', label: 'Workouts', icon: Dumbbell },
  { to: '/exercises', label: 'Exercises', icon: Library },
];

function NavIndicator({ layoutId }: { layoutId: string }) {
  return (
    <motion.span
      layoutId={layoutId}
      className="absolute inset-0 rounded-2xl bg-accent/15"
      transition={{ type: 'spring', stiffness: 380, damping: 30 }}
    />
  );
}

export default function Layout() {
  const location = useLocation();

  const isActive = (to: string) =>
    to === '/' ? location.pathname === '/' : location.pathname.startsWith(to);

  return (
    <div className="min-h-screen bg-surface-1">
      {/* ==========================================
          DESKTOP SIDEBAR (lg: 1024px+)
          ========================================== */}
      <aside className="hidden lg:flex fixed inset-y-0 left-0 z-30 w-64 bg-surface-0/80 backdrop-blur-xl border-r border-border-subtle flex-col">
        <div className="flex items-center gap-3 px-6 h-16">
          <div className="w-8 h-8 rounded-xl bg-accent/20 flex items-center justify-center">
            <Activity className="w-4 h-4 text-accent" />
          </div>
          <span className="text-lg font-bold text-text-primary tracking-tight">
            FitTrack
          </span>
        </div>

        <nav className="flex-1 px-3 py-4 space-y-1">
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className="relative flex items-center gap-3 px-3 py-2.5 rounded-2xl text-sm font-medium transition-colors duration-150"
            >
              {isActive(to) && <NavIndicator layoutId="desktop-indicator" />}
              <span className="relative z-10 flex items-center gap-3">
                <Icon
                  className={`w-[18px] h-[18px] transition-colors duration-150 ${
                    isActive(to) ? 'text-accent' : 'text-text-secondary'
                  }`}
                />
                <span
                  className={`transition-colors duration-150 ${
                    isActive(to) ? 'text-text-primary' : 'text-text-secondary'
                  }`}
                >
                  {label}
                </span>
              </span>
            </NavLink>
          ))}
        </nav>

        <div className="px-6 py-4 border-t border-border-subtle">
          <p className="text-xs text-text-tertiary">FitTrack v1.0</p>
        </div>
      </aside>

      {/* ==========================================
          TABLET NAV RAIL (sm: 640px – lg: 1023px)
          ========================================== */}
      <aside className="hidden sm:flex lg:hidden fixed inset-y-0 left-0 z-30 w-[72px] bg-surface-0/80 backdrop-blur-xl border-r border-border-subtle flex-col items-center">
        <div className="flex items-center justify-center h-16">
          <div className="w-9 h-9 rounded-xl bg-accent/20 flex items-center justify-center">
            <Activity className="w-[18px] h-[18px] text-accent" />
          </div>
        </div>

        <nav className="flex-1 flex flex-col items-center gap-2 py-4">
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className="relative flex flex-col items-center justify-center w-14 py-2 rounded-2xl transition-colors duration-150"
            >
              {isActive(to) && <NavIndicator layoutId="rail-indicator" />}
              <span className="relative z-10 flex flex-col items-center gap-0.5">
                <Icon
                  className={`w-5 h-5 transition-colors duration-150 ${
                    isActive(to) ? 'text-accent' : 'text-text-secondary'
                  }`}
                />
                <span
                  className={`text-[10px] font-medium leading-tight transition-colors duration-150 ${
                    isActive(to) ? 'text-text-primary' : 'text-text-tertiary'
                  }`}
                >
                  {label}
                </span>
              </span>
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* ==========================================
          MAIN CONTENT
          ========================================== */}
      <div className="pb-20 sm:pb-0 sm:pl-[72px] lg:pl-64 transition-[padding] duration-200">
        <main className="p-5 sm:p-6 lg:p-10 max-w-[1200px] mx-auto">
          <Outlet />
        </main>
      </div>

      {/* ==========================================
          MOBILE BOTTOM NAV BAR (< 640px)
          ========================================== */}
      <nav className="fixed bottom-0 inset-x-0 z-30 sm:hidden bg-surface-0/80 backdrop-blur-xl border-t border-border-subtle safe-area-bottom">
        <div className="flex items-center justify-around h-16">
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className="relative flex flex-col items-center justify-center min-w-[64px] py-1"
            >
              {({ isActive: active }) => (
                <>
                  {/* Indicator pill behind the icon */}
                  <span className="relative flex items-center justify-center w-16 h-8 rounded-2xl">
                    {active && <NavIndicator layoutId="mobile-indicator" />}
                    <Icon
                      className={`relative z-10 w-5 h-5 transition-colors duration-150 ${
                        active ? 'text-accent' : 'text-text-secondary'
                      }`}
                    />
                  </span>
                  <span
                    className={`mt-0.5 text-[11px] font-medium transition-colors duration-150 ${
                      active ? 'text-text-primary' : 'text-text-tertiary'
                    }`}
                  >
                    {label}
                  </span>
                </>
              )}
            </NavLink>
          ))}
        </div>
      </nav>
    </div>
  );
}
