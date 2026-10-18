import { remark } from 'remark';
import remarkGfm from 'remark-gfm';
import type { CitationBinding, ReportBlock } from '@/types/scholarflow';

const citationPattern = () => /\[@([^\]\s]+)\]/g;
type MarkdownNode = { type: string; value?: string; url?: string; title?: string | null; children?: MarkdownNode[] };
export type ReportReference = CitationBinding & { number: number };

/** Derive a reading view without changing canonical text or evidence offsets. */
export function reportPreview(blocks: ReportBlock[], bindings: CitationBinding[], active: number) {
  const byKey = new Map(bindings.map(binding => [binding.key, binding]));
  const references: ReportReference[] = [];
  const numbered = new Map<string, ReportReference>();
  // Match the server/export order across the entire current draft, including edits.
  for (const block of blocks) {
    for (const match of Array.from(block.text.matchAll(citationPattern()))) {
      const binding = byKey.get(match[1]);
      if (binding && !numbered.has(binding.key)) {
        const reference = { ...binding, number: references.length + 1 };
        numbered.set(binding.key, reference); references.push(reference);
      }
    }
  }
  const unresolved = new Set<string>();
  function referenceLink(reference: ReportReference, narrative: boolean): MarkdownNode {
    return { type: 'link', url: `#sf-reference-${reference.number}`,
      title: `查看参考文献 [${reference.number}]`,
      children: [{ type: 'text', value: `${narrative ? '文献' : ''}[${reference.number}]` }] };
  }
  function replaceText(value: string): MarkdownNode[] {
    const nodes: MarkdownNode[] = [];
    const pattern = /\[@([^\]\s]+)\]|(?<![A-Za-z0-9_])sf_[A-Za-z0-9_]+/g;
    let end = 0;
    for (const match of Array.from(value.matchAll(pattern))) {
      const start = match.index!;
      if (start > end) nodes.push({ type: 'text', value: value.slice(end, start) });
      const key = match[1] || match[0];
      let reference = match[1] ? numbered.get(key) : undefined;
      if (!match[1]) {
        // Resolve against ALL bindings so an uncited prefix collision is not hidden.
        const candidates = bindings.filter(binding => binding.key === key ||
          (/^sf_[a-fA-F0-9]{8}$/.test(key) && binding.key.startsWith(key)));
        if (candidates.length === 1) reference = numbered.get(candidates[0].key);
      }
      if (reference) nodes.push(referenceLink(reference, !match[1]));
      else { unresolved.add(match[0]); nodes.push({ type: 'text', value: match[0] }); }
      end = start + match[0].length;
    }
    if (end < value.length) nodes.push({ type: 'text', value: value.slice(end) });
    return nodes;
  }
  function visit(node: MarkdownNode) {
    // Do not alter code, link labels/destinations, images or raw HTML.
    if (['code', 'inlineCode', 'link', 'linkReference', 'image', 'imageReference', 'html'].includes(node.type)) return;
    if (node.children) node.children = node.children.flatMap(child => {
      if (child.type === 'text') return replaceText(child.value || '');
      visit(child); return [child];
    });
  }
  const processor = remark().use(remarkGfm);
  const tree = processor.parse(blocks[active]?.text || '');
  visit(tree);
  return { markdown: processor.stringify(tree), references, unresolved: Array.from(unresolved) };
}
