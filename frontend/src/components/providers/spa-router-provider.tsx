"use client";

import * as React from "react";
import {
  BrowserRouter,
  MemoryRouter,
  useNavigate as useRrdNavigate,
  useLocation as useRrdLocation,
  useParams as useRrdParams,
  useSearchParams as useRrdSearchParams,
  Link as RrdLink,
  NavLink as RrdNavLink,
  useInRouterContext,
  type NavigateOptions,
} from "react-router-dom";
import {
  useRouter as useNextRouter,
  usePathname as useNextPathname,
} from "next/navigation";

// Export standard react-router-dom hooks and types for all pages
export {
  useRrdNavigate as useNavigate,
  useRrdLocation as useLocation,
  useRrdParams as useParams,
  useRrdSearchParams as useSearchParams,
  RrdLink as Link,
  RrdNavLink as NavLink,
};

export interface SpaRouterContextType {
  push: (href: string) => void;
  replace: (href: string) => void;
  prefetch?: (href: string) => void;
  pathname: string;
  navigate: (to: string | number, options?: NavigateOptions) => void;
  location: { pathname: string; search: string; hash: string };
}

const SpaRouterContext = React.createContext<SpaRouterContextType | null>(null);

function InnerSync({ children }: { children: React.ReactNode }) {
  const rrdNavigate = useRrdNavigate();
  const rrdLocation = useRrdLocation();
  const nextRouter = useNextRouter();
  const nextPathname = useNextPathname();

  // Keep Next.js pathname and React Router location synchronized
  React.useEffect(() => {
    if (nextPathname && rrdLocation.pathname !== nextPathname) {
      rrdNavigate(nextPathname, { replace: true });
    }
  }, [nextPathname, rrdLocation.pathname, rrdNavigate]);

  // Expose router instance globally on window for easy verification and debugging in Console
  React.useEffect(() => {
    if (typeof window !== "undefined") {
      (window as unknown as Record<string, unknown>).__REACT_ROUTER__ = {
        active: true,
        pathname: nextPathname || rrdLocation.pathname,
        location: rrdLocation,
        navigate: (to: string) => rrdNavigate(to),
      };
    }
  }, [nextPathname, rrdLocation, rrdNavigate]);

  const push = React.useCallback(
    (href: string) => {
      try {
        rrdNavigate(href);
      } catch {
        // Fallback if out of context
      }
      if (nextRouter && typeof nextRouter.push === "function") {
        nextRouter.push(href, { scroll: false });
      }
    },
    [rrdNavigate, nextRouter]
  );

  const replace = React.useCallback(
    (href: string) => {
      try {
        rrdNavigate(href, { replace: true });
      } catch {
        // Fallback if out of context
      }
      if (nextRouter && typeof nextRouter.replace === "function") {
        nextRouter.replace(href, { scroll: false });
      }
    },
    [rrdNavigate, nextRouter]
  );

  const prefetch = React.useCallback(
    (href: string) => {
      try {
        if (nextRouter && typeof nextRouter.prefetch === "function") {
          nextRouter.prefetch(href);
        }
      } catch {
        // Ignore prefetch errors
      }
    },
    [nextRouter]
  );

  const navigate = React.useCallback(
    (to: string | number, options?: NavigateOptions) => {
      if (typeof to === "number") {
        try {
          rrdNavigate(to);
        } catch {
          if (typeof window !== "undefined") window.history.go(to);
        }
        return;
      }
      if (options?.replace) {
        replace(to);
      } else {
        push(to);
      }
    },
    [rrdNavigate, push, replace]
  );

  return (
    <SpaRouterContext.Provider
      value={{
        push,
        replace,
        prefetch,
        pathname: nextPathname || rrdLocation.pathname,
        navigate,
        location: {
          pathname: nextPathname || rrdLocation.pathname,
          search: rrdLocation.search,
          hash: rrdLocation.hash,
        },
      }}
    >
      {children}
    </SpaRouterContext.Provider>
  );
}

export function SpaRouterProvider({ children }: { children: React.ReactNode }) {
  const [mounted, setMounted] = React.useState(false);

  React.useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) {
    // During SSR or initial pre-hydration pass, use MemoryRouter to prevent "document is not defined"
    return (
      <MemoryRouter>
        <InnerSync>{children}</InnerSync>
      </MemoryRouter>
    );
  }

  return (
    <BrowserRouter>
      <InnerSync>{children}</InnerSync>
    </BrowserRouter>
  );
}

export function useSpaRouter(): SpaRouterContextType {
  const ctx = React.useContext(SpaRouterContext);
  let nextRouter: ReturnType<typeof useNextRouter> | null = null;
  try {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    nextRouter = useNextRouter();
  } catch {
    nextRouter = null;
  }

  if (ctx) return ctx;

  const push = (href: string) => {
    if (nextRouter && typeof nextRouter.push === "function") {
      nextRouter.push(href);
    } else if (typeof window !== "undefined") {
      window.history.pushState(null, "", href);
    }
  };

  const replace = (href: string) => {
    if (nextRouter && typeof nextRouter.replace === "function") {
      nextRouter.replace(href, { scroll: false });
    } else if (typeof window !== "undefined") {
      window.history.replaceState(null, "", href);
    }
  };

  const prefetch = (href: string) => {
    if (nextRouter && typeof nextRouter.prefetch === "function") {
      try {
        nextRouter.prefetch(href);
      } catch {
        // Ignore
      }
    }
  };

  return {
    push,
    replace,
    prefetch,
    pathname: typeof window !== "undefined" ? window.location.pathname : "/",
    navigate: (to: string | number, options?: NavigateOptions) => {
      if (typeof to === "number") {
        if (typeof window !== "undefined") window.history.go(to);
      } else if (options?.replace) {
        replace(to);
      } else {
        push(to);
      }
    },
    location: {
      pathname: typeof window !== "undefined" ? window.location.pathname : "/",
      search: typeof window !== "undefined" ? window.location.search : "",
      hash: typeof window !== "undefined" ? window.location.hash : "",
    },
  };
}

export interface SpaLinkProps extends React.AnchorHTMLAttributes<HTMLAnchorElement> {
  href: string;
  children: React.ReactNode;
}

export const SpaLink = React.forwardRef<HTMLAnchorElement, SpaLinkProps>(
  ({ href, onClick, children, className, ...props }, ref) => {
    const router = useSpaRouter();
    let inRouter = false;
    try {
      // eslint-disable-next-line react-hooks/rules-of-hooks
      inRouter = useInRouterContext();
    } catch {
      inRouter = false;
    }

    const handleMouseEnter = (e: React.MouseEvent<HTMLAnchorElement>) => {
      if (props.onMouseEnter) props.onMouseEnter(e);
      if (href && href.startsWith("/") && router.prefetch) {
        router.prefetch(href);
      }
    };

    const handleClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
      if (onClick) onClick(e);
      // Let browser open new tab on modifier clicks or explicit target="_blank"
      if (
        e.defaultPrevented ||
        e.metaKey ||
        e.ctrlKey ||
        e.shiftKey ||
        e.altKey ||
        props.target === "_blank"
      ) {
        return;
      }
      e.preventDefault();
      router.push(href);
    };

    if (inRouter) {
      return (
        <RrdLink
          to={href}
          ref={ref}
          onClick={handleClick}
          onMouseEnter={handleMouseEnter}
          className={className}
          {...props}
        >
          {children}
        </RrdLink>
      );
    }

    return (
      <a
        href={href}
        ref={ref}
        onClick={handleClick}
        onMouseEnter={handleMouseEnter}
        className={className}
        {...props}
      >
        {children}
      </a>
    );
  }
);
SpaLink.displayName = "SpaLink";
