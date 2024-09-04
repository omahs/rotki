import { type Section, Status } from '@/types/status';

interface Opts {
  section?: Section;
  subsection?: string;
}

export function useStatusUpdater(defaultSection: Section) {
  const { setStatus, getStatus, isLoading } = useStatusStore();
  const updateStatus = (status: Status, opts: Opts = {}): void => {
    const { section = defaultSection, subsection } = opts;

    setStatus({
      section,
      subsection,
      status,
    });
  };

  const resetStatus = (opts: Opts = {}): void => {
    const { section = defaultSection, subsection } = opts;

    setStatus({
      section,
      subsection,
      status: Status.NONE,
    });
  };

  const loading = (opts: Opts = {}): boolean => {
    const { section = defaultSection, subsection } = opts;
    return get(isLoading(section, subsection));
  };

  const isFirstLoad = (opts: Opts = {}): boolean => {
    const { section = defaultSection, subsection } = opts;
    return get(getStatus(section, subsection)) === Status.NONE;
  };

  const fetchDisabled = (refresh: boolean, opts: Opts = {}): boolean => !(isFirstLoad(opts) || refresh) || loading(opts);

  const getSectionStatus = (opts: Opts = {}): Status => {
    const { section = defaultSection, subsection } = opts;
    return get(getStatus(section, subsection));
  };

  return {
    loading,
    isFirstLoad,
    setStatus: updateStatus,
    getStatus: getSectionStatus,
    fetchDisabled,
    resetStatus,
  };
}
