/// <reference types="vitest" />
import "@testing-library/jest-dom/vitest";

// jsdom has no layout, so scrolling is a stub the tests can spy on.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}
