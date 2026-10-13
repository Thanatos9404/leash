import { ProductApp } from "./app/ProductApp";
import { Landing } from "./landing/Landing";
import { useLocation } from "./lib/router";

export function App() {
  const { path } = useLocation();
  return path === "/app" || path.startsWith("/app/") ? <ProductApp /> : <Landing />;
}
