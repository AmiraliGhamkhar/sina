import type { ReactNode } from "react";
import { Brain } from "lucide-react";

interface AuthLayoutProps {
  title: string;
  subtitle: string;
  children: ReactNode;
  footer: ReactNode;
}

export function AuthLayout({ title, subtitle, children, footer }: AuthLayoutProps) {
  return (
    <div className="flex min-h-screen w-screen items-center justify-center p-4">
      <div className="w-full max-w-[420px] animate-scale-in">
        <div className="rounded-xl border border-border bg-bg-secondary p-7 shadow-[0_20px_60px_-20px_rgba(0,0,0,0.7)]">
          <div className="mb-6 flex flex-col items-center text-center">
            <span className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-bg-tertiary">
              <Brain size={24} className="text-accent-primary" />
            </span>
            <h1 className="text-[2rem] leading-tight text-text-primary">{title}</h1>
            <p className="mt-1 text-sm text-text-secondary">{subtitle}</p>
          </div>
          {children}
        </div>
        <p className="mt-5 text-center text-sm text-text-secondary">{footer}</p>
      </div>
    </div>
  );
}
