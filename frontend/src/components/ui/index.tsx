/** Design-system primitives in the shadcn/ui style, built on Radix. */
import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as DropdownPrimitive from "@radix-ui/react-dropdown-menu";
import * as SwitchPrimitive from "@radix-ui/react-switch";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { Loader2, X } from "lucide-react";
import * as React from "react";
import { cn } from "@/utils";

const buttonStyles = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-[13px] font-medium transition-colors disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-3.5 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-primary text-primary-foreground hover:bg-primary/90",
        secondary: "border bg-background hover:bg-accent shadow-card",
        ghost: "hover:bg-accent",
        danger: "border border-danger/30 text-danger hover:bg-danger/10",
      },
      size: { sm: "h-7 px-2.5", md: "h-8 px-3", lg: "h-10 px-4 text-sm", icon: "size-8" },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonStyles> {
  asChild?: boolean;
  loading?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, asChild, loading, children, disabled, type, ...props }, ref) => {
  if (asChild) return <Slot ref={ref} className={cn(buttonStyles({ variant, size }), className)} {...props}>{children}</Slot>;
  return (
    <button ref={ref} type={type ?? "button"} disabled={disabled || loading} className={cn(buttonStyles({ variant, size }), className)} {...props}>
      {loading && <Loader2 className="animate-spin" />}
      {children}
    </button>
  );
});
Button.displayName = "Button";

const fieldStyles = "w-full rounded-md border border-input bg-background px-2.5 text-[13px] placeholder:text-muted-foreground disabled:opacity-60";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(({ className, ...props }, ref) => <input ref={ref} className={cn(fieldStyles, "h-8", className)} {...props} />);
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className, ...props }, ref) => <textarea ref={ref} className={cn(fieldStyles, "min-h-20 py-1.5 leading-relaxed", className)} {...props} />);
Textarea.displayName = "Textarea";

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(({ className, ...props }, ref) => <select ref={ref} className={cn(fieldStyles, "h-8 pr-6", className)} {...props} />);
Select.displayName = "Select";

export function Field({ label, error, hint, htmlFor, children }: { label: string; error?: string; hint?: string; htmlFor?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <label htmlFor={htmlFor} className="block text-xs font-medium">{label}</label>
      {children}
      {error ? <p role="alert" className="text-xs text-danger">{error}</p> : hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

export const Card = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => <div className={cn("rounded-lg border bg-background shadow-card", className)} {...props} />;

export function CardHeader({ title, description, action, className }: { title: React.ReactNode; description?: React.ReactNode; action?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-start justify-between gap-3 border-b px-4 py-3", className)}>
      <div className="min-w-0">
        <h3 className="text-[13px] font-semibold">{title}</h3>
        {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  );
}

const badgeStyles = cva("inline-flex items-center gap-1 rounded px-1.5 py-px text-2xs font-medium whitespace-nowrap [&_svg]:size-3", {
  variants: {
    tone: {
      neutral: "bg-muted text-muted-foreground",
      primary: "bg-primary/10 text-primary",
      success: "bg-success/10 text-success",
      warning: "bg-warning/10 text-warning",
      danger: "bg-danger/10 text-danger",
    },
  },
  defaultVariants: { tone: "neutral" },
});
export const Badge = ({ className, tone, ...props }: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeStyles>) => <span className={cn(badgeStyles({ tone }), className)} {...props} />;

export const Skeleton = ({ className }: { className?: string }) => <div aria-hidden className={cn("animate-pulse rounded-md bg-muted", className)} />;

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export function DialogContent({ title, description, children, className }: { title: string; description?: string; children: React.ReactNode; className?: string }) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
      <DialogPrimitive.Content
        aria-describedby={description ? undefined : undefined}
        className={cn("fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border bg-popover p-5 shadow-pop data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-[.98]", className)}
      >
        <DialogPrimitive.Title className="pr-6 text-sm font-semibold">{title}</DialogPrimitive.Title>
        {description ? <DialogPrimitive.Description className="mt-1 text-[13px] text-muted-foreground">{description}</DialogPrimitive.Description> : <DialogPrimitive.Description className="sr-only">{title}</DialogPrimitive.Description>}
        <div className="mt-4">{children}</div>
        <DialogPrimitive.Close aria-label="Close" className="absolute right-3 top-3 rounded p-1 text-muted-foreground hover:bg-accent"><X className="size-4" /></DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

export function ConfirmDialog({ open, onOpenChange, title, description, confirmLabel = "Confirm", onConfirm, loading }: { open: boolean; onOpenChange: (open: boolean) => void; title: string; description: string; confirmLabel?: string; onConfirm: () => void; loading?: boolean }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={title} description={description}>
        <div className="flex justify-end gap-2">
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="danger" loading={loading} onClick={onConfirm}>{confirmLabel}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export const DropdownMenu = DropdownPrimitive.Root;
export const DropdownTrigger = DropdownPrimitive.Trigger;
export function DropdownContent({ className, children, align = "end" }: { className?: string; children: React.ReactNode; align?: "start" | "end" }) {
  return (
    <DropdownPrimitive.Portal>
      <DropdownPrimitive.Content align={align} sideOffset={6} className={cn("z-50 min-w-44 rounded-md border bg-popover p-1 shadow-pop data-[state=open]:animate-in data-[state=open]:fade-in-0", className)}>
        {children}
      </DropdownPrimitive.Content>
    </DropdownPrimitive.Portal>
  );
}
export const DropdownItem = ({ className, ...props }: React.ComponentProps<typeof DropdownPrimitive.Item>) => <DropdownPrimitive.Item className={cn("flex cursor-default select-none items-center gap-2 rounded px-2 py-1.5 text-[13px] outline-none data-[highlighted]:bg-accent [&_svg]:size-3.5", className)} {...props} />;
export const DropdownSeparator = () => <DropdownPrimitive.Separator className="my-1 h-px bg-border" />;

export const Switch = React.forwardRef<HTMLButtonElement, React.ComponentProps<typeof SwitchPrimitive.Root>>(({ className, ...props }, ref) => (
  <SwitchPrimitive.Root ref={ref} className={cn("inline-flex h-[18px] w-8 shrink-0 cursor-pointer items-center rounded-full bg-input transition-colors disabled:opacity-50 data-[state=checked]:bg-primary", className)} {...props}>
    <SwitchPrimitive.Thumb className="block size-3.5 translate-x-0.5 rounded-full bg-white shadow transition-transform data-[state=checked]:translate-x-[15px]" />
  </SwitchPrimitive.Root>
));
Switch.displayName = "Switch";

export const Tabs = TabsPrimitive.Root;
export const TabsContent = TabsPrimitive.Content;
export const TabsList = ({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.List>) => <TabsPrimitive.List className={cn("flex gap-1 overflow-x-auto overflow-y-hidden border-b", className)} {...props} />;
export const TabsTrigger = ({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Trigger>) => <TabsPrimitive.Trigger className={cn("-mb-px flex items-center gap-1.5 whitespace-nowrap border-b-2 border-transparent px-2.5 py-2 text-[13px] font-medium text-muted-foreground hover:text-foreground data-[state=active]:border-primary data-[state=active]:text-foreground", className)} {...props} />;
