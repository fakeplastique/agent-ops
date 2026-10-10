"use client";

import { useId, useRef, useState, type FormEvent } from "react";

import { AuthFrame, Notice, primaryButton, secondaryButton } from "@/app/(auth)/AuthFrame";
import { ReadView } from "@/components/states/States";
import { api, ApiError } from "@/lib/api/client";
import { enterOrganization, signOut, useSession } from "@/lib/session";

// Same rule as the API (members._SLUG).
const SLUG = /^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$/;
const SLUG_RULE = "Use 3 to 40 lowercase letters, digits and hyphens, starting and ending with a letter or digit.";

const slugify = (name: string) =>
  name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .slice(0, 40)
    .replace(/^-+|-+$/g, "");

const input =
  "h-control w-full rounded-control border border-control bg-surface px-3 text-text max-lg:h-touch aria-invalid:border-negative";

function Field({
  label,
  hint,
  error,
  inputRef,
  ...props
}: { label: string; hint?: string; error?: string; inputRef?: React.Ref<HTMLInputElement> } & React.InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  const note = error ?? hint;
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="font-medium">
        {label}
      </label>
      <input
        id={id}
        ref={inputRef}
        aria-invalid={error ? true : undefined}
        aria-describedby={note ? `${id}-note` : undefined}
        className={input}
        {...props}
      />
      {note && (
        <p id={`${id}-note`} className={`m-0 text-small ${error ? "text-negative" : "text-muted"}`}>
          {note}
        </p>
      )}
    </div>
  );
}

function CreateOrganization() {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);
  const [errors, setErrors] = useState<{ name?: string; slug?: string; form?: string }>({});
  const [busy, setBusy] = useState(false);
  const slugRef = useRef<HTMLInputElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const shownSlug = slugEdited ? slug : slugify(name);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    const found = {
      name: name.trim() ? undefined : "Enter the organization's name.",
      slug: SLUG.test(shownSlug) ? undefined : SLUG_RULE,
    };
    if (found.name || found.slug) {
      setErrors(found);
      (found.name ? nameRef : slugRef).current?.focus();
      return;
    }
    setErrors({});
    setBusy(true);
    try {
      await api("/organizations", { method: "POST", body: { name: name.trim(), slug: shownSlug } });
    } catch (err) {
      setBusy(false);
      if (err instanceof ApiError && err.code === "slug_taken") {
        setErrors({ slug: "That slug is taken. Try another." });
        slugRef.current?.focus();
      } else setErrors({ form: err instanceof ApiError && err.status === 422 ? err.message : "Check your connection, then try again." });
      return;
    }
    enterOrganization(); // the API switched the session to the new organization
  };

  return (
    <form noValidate onSubmit={(e) => void submit(e)} className="flex flex-col gap-4">
      {errors.form && <Notice title="The organization was not created">{errors.form}</Notice>}
      <Field
        label="Name"
        inputRef={nameRef}
        value={name}
        maxLength={80}
        autoComplete="organization"
        error={errors.name}
        onChange={(e) => setName(e.target.value)}
      />
      <Field
        label="Slug"
        inputRef={slugRef}
        value={shownSlug}
        maxLength={40}
        autoComplete="off"
        spellCheck={false}
        hint="Lowercase letters, digits and hyphens. It names the organization in links."
        error={errors.slug}
        onChange={(e) => {
          setSlugEdited(true);
          setSlug(e.target.value);
        }}
      />
      <button type="submit" aria-disabled={busy} className={`self-start ${primaryButton}`}>
        {busy ? "Creating…" : "Create organization"}
      </button>
    </form>
  );
}

/** First stop for a signed-in user with no organization: create one, or use an invitation link. Outside the app shell. */
export function Onboarding() {
  const session = useSession();
  return (
    <AuthFrame title="Set up your organization" lead="Create an organization for your team. You become its admin.">
      <ReadView read={session} label="Create an organization" loading={<p className="m-0 text-muted">Checking your session…</p>}>
        {() => <CreateOrganization />}
      </ReadView>
      <div className="flex flex-col items-start gap-3 border-t border-rule pt-4">
        <p className="m-0 text-muted">Invited to an existing organization? Open the invitation link your admin sent you.</p>
        <button type="button" onClick={() => void signOut()} className={secondaryButton}>
          Sign out
        </button>
      </div>
    </AuthFrame>
  );
}
