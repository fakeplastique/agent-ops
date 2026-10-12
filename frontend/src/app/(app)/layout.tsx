import { AppShell } from "@/components/shell/AppShell";

// Every screen behind sign-in lives in this group; login, onboarding and invitations stay in (auth), outside the shell.
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
