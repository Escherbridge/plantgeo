import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { CopyShareText } from '@/components/panels/CopyShareText';
import { ResumeConversation } from '@/app/dashboard/conversations/ResumeConversation';
import { useRegionalIntelligenceStore } from '@/stores/regional-intelligence-store';

const mocks = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mocks.push }) }));
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); useRegionalIntelligenceStore.getState().closePanel(); });

describe('explicit private chat text controls', () => {
  it('does not copy or share until the user chooses an action', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    const share = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText }, share });
    render(<CopyShareText text="Historical private report" title="Saved analysis" />);
    expect(writeText).not.toHaveBeenCalled();
    expect(share).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Copy text' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('Historical private report'));
    fireEvent.click(screen.getByRole('button', { name: 'Share text' }));
    await waitFor(() => expect(share).toHaveBeenCalledWith({ title: 'Saved analysis', text: 'Historical private report' }));
    expect(share.mock.calls[0][0]).not.toHaveProperty('url');
  });
  it('offers copied text for sharing when native share is unavailable and reports copy failures', async () => {
    const writeText = vi.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('denied'));
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    render(<CopyShareText text="Saved report" />);
    fireEvent.click(screen.getByRole('button', { name: 'Share text' }));
    await screen.findByText(/Text copied for sharing/);
    fireEvent.click(screen.getByRole('button', { name: 'Copy text' }));
    await screen.findByText(/Copy is unavailable/);
  });
  it('resumes saved messages at their original map location without making an analysis request', () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    render(<ResumeConversation id="owned" lat={44.66} lon={-118.83} mapHref="/?focusLat=44.66&focusLng=-118.83" messages={[{ id: 'saved', savedMessageId: 'saved', role: 'assistant', content: 'As recorded' }]} />);
    expect(useRegionalIntelligenceStore.getState().isOpen).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Resume on map' }));
    expect(useRegionalIntelligenceStore.getState()).toMatchObject({ conversationId: 'owned', isLoading: false, messages: [{ content: 'As recorded', savedMessageId: 'saved' }] });
    expect(mocks.push).toHaveBeenCalledWith('/?focusLat=44.66&focusLng=-118.83');
    expect(fetch).not.toHaveBeenCalled();
  });
});
