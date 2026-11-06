// Exercise actual editor conversion functions with Node's built-in assertions.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/src/components/TemplateEditor.jsx'), 'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('function normalizeTemplate('), source.indexOf('export default function TemplateEditor')), context);
const convert = input => JSON.parse(JSON.stringify(context.denormalizeTemplate(context.normalizeTemplate(input))));
const field = { type: 'barcode', barcode: true, pattern: '.+', x_min: 0, x_max: 500, y_min: 0, y_max: 200 };
for (const section of ['header', 'footer']) {
  const input = { name: 'test', [section]: { fields: { code: field } } };
  const normalized = context.normalizeTemplate(input);
  assert.equal(context.hasTemplateContent(normalized), true);
  const result = convert(input);
  for (const other of ['header', 'table', 'footer'].filter(s => s !== section)) {
    assert.equal(Object.hasOwn(result, other), false);
    assert.equal(normalized[other], undefined);
  }
  assert.equal(result[section].fields.code.barcode, true);
}
assert.equal(context.hasTemplateContent(context.normalizeTemplate({ name: 'empty' })), false);
assert.equal(context.hasTemplateContent(context.normalizeTemplate({ header: { fields: { ' ': field } } })), false);
const complete = { name: 'full', header: { fields: { code: field } }, table: { columns: [{ name: 'item', value: 'first_word' }] }, footer: { fields: {} } };
assert.equal(context.hasTemplateContent(context.normalizeTemplate(complete)), true);
for (const section of ['header', 'table', 'footer']) assert.ok(convert(complete)[section]);
const removed = context.normalizeTemplate({ name: 'legacy', header_fields: { code: field } });
delete removed.header;
delete removed.header_fields;
assert.equal(Object.hasOwn(JSON.parse(JSON.stringify(context.denormalizeTemplate(removed))), 'header'), false);
assert.equal(Object.hasOwn(convert({ table: { columns: [{ name: 'item' }] } }), 'footer'), false);
console.log('Template section conversion checks: OK');