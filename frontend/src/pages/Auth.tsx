import { CheckCircle2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Button, Field, Input } from "@/components/ui";
import { useAuth } from "@/contexts";
import { ApiError, api } from "@/services/api";
import type { User } from "@/types";
import { errorMessage } from "@/utils";

const POINTS = ["Delegates email, calendar, tasks and documents to specialized agents", "Sensitive actions wait for your approval, every time", "Every action is logged, and failures are reported plainly"];

function Shell({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return (
    <div className="grid min-h-full lg:grid-cols-2">
      <div className="hidden flex-col justify-between border-r bg-surface p-10 lg:flex">
        <div className="flex items-center gap-2 font-semibold tracking-tight"><img src="/favicon.svg" alt="" className="size-6" />AutoPilot AI</div>
        <div>
          <p className="max-w-md text-2xl font-semibold leading-snug tracking-tight">Your work. Your AI employee. Fully in control.</p>
          <ul className="mt-6 space-y-2.5">{POINTS.map((p) => <li key={p} className="flex gap-2 text-[13px] text-muted-foreground"><CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-primary" />{p}</li>)}</ul>
        </div>
        <span />
      </div>
      <div className="flex items-center justify-center p-6">
        <div className="w-full max-w-sm">
          <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
          <p className="mb-5 mt-0.5 text-[13px] text-muted-foreground">{subtitle}</p>
          {children}
        </div>
      </div>
    </div>
  );
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function validateRegistration(values: { name: string; email: string; password: string }): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!values.name.trim()) errors.name = "Enter your name.";
  if (!EMAIL_RE.test(values.email)) errors.email = "Enter a valid email address.";
  if (values.password.length < 10) errors.password = "Use at least 10 characters.";
  else if (!/[a-z]/i.test(values.password) || !/\d/.test(values.password)) errors.password = "Include both letters and numbers.";
  return errors;
}

export function LoginPage() {
  const { setUser } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!EMAIL_RE.test(email) || !password) return setError("Enter your email and password.");
    setBusy(true);
    setError("");
    try {
      setUser(await api.post<User>("/auth/login", { email, password }));
      navigate("/", { replace: true });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Shell title="Sign in" subtitle="Welcome back to your AI employee.">
      <form onSubmit={submit} className="space-y-3" noValidate>
        <Field label="Email" htmlFor="email"><Input id="email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} className="h-9" /></Field>
        <Field label="Password" htmlFor="password"><Input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} className="h-9" /></Field>
        {error && <p role="alert" className="text-xs text-danger">{error}</p>}
        <Button type="submit" variant="primary" size="lg" className="w-full" loading={busy}>Sign in</Button>
      </form>
      <p className="mt-4 text-center text-[13px] text-muted-foreground">New here? <Link to="/register" className="text-primary hover:underline">Create an account</Link></p>
    </Shell>
  );
}

export function RegisterPage() {
  const { setUser } = useAuth();
  const navigate = useNavigate();
  const [values, setValues] = useState({ name: "", email: "", password: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (key: keyof typeof values) => (e: React.ChangeEvent<HTMLInputElement>) => setValues((v) => ({ ...v, [key]: e.target.value }));
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const found = validateRegistration(values);
    setErrors(found);
    setFormError("");
    if (Object.keys(found).length) return;
    setBusy(true);
    try {
      setUser(await api.post<User>("/auth/register", { full_name: values.name, email: values.email, password: values.password, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone }));
      navigate("/", { replace: true });
    } catch (e) {
      if (e instanceof ApiError && e.code === "email_taken") setErrors({ email: e.message });
      else setFormError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Shell title="Create your account" subtitle="Start with simulated data. Connect your real accounts when you're ready.">
      <form onSubmit={submit} className="space-y-3" noValidate>
        <Field label="Name" error={errors.name} htmlFor="name"><Input id="name" autoComplete="name" value={values.name} onChange={set("name")} className="h-9" /></Field>
        <Field label="Email" error={errors.email} htmlFor="email"><Input id="email" type="email" autoComplete="email" value={values.email} onChange={set("email")} className="h-9" /></Field>
        <Field label="Password" error={errors.password} hint="At least 10 characters, with letters and numbers" htmlFor="password"><Input id="password" type="password" autoComplete="new-password" value={values.password} onChange={set("password")} className="h-9" /></Field>
        {formError && <p role="alert" className="text-xs text-danger">{formError}</p>}
        <Button type="submit" variant="primary" size="lg" className="w-full" loading={busy}>Create account</Button>
      </form>
      <p className="mt-4 text-center text-[13px] text-muted-foreground">Already have an account? <Link to="/login" className="text-primary hover:underline">Sign in</Link></p>
    </Shell>
  );
}
