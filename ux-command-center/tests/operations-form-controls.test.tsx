import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render } from '@testing-library/react';
import { FormSelect, FormInput } from '../components/operations';

// Regression for issue #4: "Cannot classify new assets or change asset's class".
// FormSelect/FormInput previously forwarded the raw ChangeEvent to onChange, but
// every caller (AssetAudit Classify modal) expects the VALUE string
// (e.g. onChange={(v) => ...class_id: Number(v)}). The mismatch made the Target
// Class selector appear unresponsive (Number(event) === NaN).
describe('operations form controls onChange contract (issue #4)', () => {
  it('FormSelect onChange receives the selected value string, not the event', () => {
    const onChange = vi.fn();
    const { getByRole } = render(
      <FormSelect
        value=""
        onChange={onChange}
        options={[
          { value: '', label: 'Select class…' },
          { value: '3', label: 'US Equity (美股)' },
        ]}
      />,
    );
    fireEvent.change(getByRole('combobox'), { target: { value: '3' } });
    expect(onChange).toHaveBeenCalledWith('3');
  });

  it('FormInput onChange receives the typed value string, not the event', () => {
    const onChange = vi.fn();
    const { getByRole } = render(<FormInput value="" onChange={onChange} />);
    fireEvent.change(getByRole('textbox'), { target: { value: 'US_STK_BRK-B' } });
    expect(onChange).toHaveBeenCalledWith('US_STK_BRK-B');
  });
});
