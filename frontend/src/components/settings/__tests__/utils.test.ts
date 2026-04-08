import { PIPELINE_PHASES } from '../utils';

describe('PIPELINE_PHASES', () => {
  it('has 6 phases in expected order', () => {
    expect(PIPELINE_PHASES).toHaveLength(6);
    expect(PIPELINE_PHASES.map((phase) => phase.id)).toEqual(['sync', 'artist', 'album', 'artwork', 'post', 'tags']);
  });

  it('has expected worker counts for bar phases', () => {
    expect(PIPELINE_PHASES.find((phase) => phase.id === 'artist')?.workers).toHaveLength(5);
    expect(PIPELINE_PHASES.find((phase) => phase.id === 'album')?.workers).toHaveLength(2);
    expect(PIPELINE_PHASES.find((phase) => phase.id === 'artwork')?.workers).toHaveLength(3);
    expect(PIPELINE_PHASES.find((phase) => phase.id === 'tags')?.workers).toHaveLength(9);
  });

  it('post phase is checklist with expected substeps', () => {
    const post = PIPELINE_PHASES.find((phase) => phase.id === 'post');
    expect(post?.displayType).toBe('checklist');
    expect(post?.substeps).toEqual([
      { key: 'ownership_sync', label: 'Ownership' },
      { key: 'normalize_titles', label: 'Title Normalization' },
      { key: 'missing_counts', label: 'Missing Counts' },
      { key: 'canonical', label: 'Canonical Grouping' },
    ]);
  });

  it('does not include deprecated worker keys', () => {
    const keys = PIPELINE_PHASES.flatMap((phase) => phase.workers.map((worker) => worker.key));
    expect(keys).not.toContain('spotify_id');
    expect(keys).not.toContain('lastfm_id');
  });
});
