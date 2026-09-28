import { useEffect } from 'react';

// Set the browser tab / SEO title per route. Every rendered page calls this,
// so the title always reflects the active route (the SPA shell title in
// index.html is only the initial paint).
export default function useDocumentTitle(title) {
  useEffect(() => {
    document.title = title;
  }, [title]);
}
