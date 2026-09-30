"use client";


type ExploreHeaderProps = {
  locationName: string | null;
};

export default function ExploreHeader({
  locationName,
}: ExploreHeaderProps) {
  return (
    <>
      <header className="border-b border-white/[0.06]">

        <div className="mx-auto flex max-w-[1440px] items-center justify-between px-6 py-6 md:px-10 lg:px-14">

          <div>

            <p className="text-[12px] uppercase tracking-[0.22em] text-white/60">
              Explore
            </p>

            <h1 className="mt-1 text-[22px] font-semibold tracking-[-0.03em]">
              Find your trail.
            </h1>

          </div>


          {locationName && (
            <div className="hidden max-w-[500px] truncate rounded-full border border-white/10 bg-white/[0.04] px-4 py-2 text-[13px] text-white/75 md:block">
              {
                locationName
              }
            </div>
          )}

        </div>

      </header>
    </>
  );
}
