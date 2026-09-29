"use client";

import {
  FormEvent,
  useState,
} from "react";

import Link from "next/link";
import { useRouter } from "next/navigation";


export default function Home() {

  const router = useRouter();

  const [
    query,
    setQuery,
  ] = useState("");


  function handleSubmit(
    event: FormEvent<HTMLFormElement>
  ) {

    event.preventDefault();


    const trimmedQuery =
      query.trim();


    if (!trimmedQuery) {
      return;
    }


    router.push(
      `/explore?query=${encodeURIComponent(
        trimmedQuery
      )}`
    );
  }


  return (
    <main className="min-h-screen bg-[#07111f] text-white">

      <section className="relative min-h-screen overflow-hidden">

        {/* Background image */}

        <div
          className="absolute inset-0 bg-cover bg-center"
          style={{
            backgroundImage:
              "url('https://images.unsplash.com/photo-1464822759023-fed622ff2c3b?auto=format&fit=crop&w=2000&q=85')",
          }}
        />


        {/* Dark overlay */}

        <div className="absolute inset-0 bg-[#07111f]/65" />

        <div className="absolute inset-0 bg-gradient-to-b from-[#07111f]/40 via-[#07111f]/55 to-[#07111f]" />


        <div className="relative z-10 mx-auto flex min-h-screen max-w-[1400px] flex-col px-6 md:px-10 lg:px-14">

          {/* ============================================================
              HEADER
          ============================================================ */}

          <header className="pt-6">

            <div className="flex items-center justify-between rounded-2xl border border-white/10 bg-black/20 px-5 py-4 shadow-[0_20px_60px_rgba(0,0,0,0.20)] backdrop-blur-xl">

              <Link
                href="/"
                className="flex items-center gap-3"
              >

                <div className="flex h-10 w-10 items-center justify-center rounded-full border border-[#c8a66a]/30 bg-[#c8a66a]/10 text-lg">
                  ⛰
                </div>


                <div>

                  <p className="text-sm font-semibold tracking-[0.08em]">
                    GoBeyond
                  </p>

                  <p className="mt-0.5 text-[10px] uppercase tracking-[0.18em] text-white/40">
                    Trail Intelligence
                  </p>

                </div>

              </Link>


              <nav className="flex items-center gap-7 text-sm text-white/60">

                <Link
                  href="/explore"
                  className="transition hover:text-white"
                >
                  Explore
                </Link>


                {/*
                  Only real destinations belong in the nav. A styled
                  placeholder that looks clickable and does nothing is worse
                  than no item at all.
                */}
                <a
                  href="#what-you-get"
                  className="transition hover:text-white"
                >
                  What you get
                </a>

              </nav>

            </div>

          </header>


          {/* ============================================================
              HERO
          ============================================================ */}

          <section className="flex flex-1 flex-col items-center justify-center pb-20 pt-16 text-center">

            <p className="mb-6 text-xs font-medium uppercase tracking-[0.28em] text-[#c8a66a] md:text-sm">
              Verified OpenStreetMap routes
            </p>


          <h1 className="max-w-4xl text-4xl font-semibold leading-[1.02] tracking-[-0.02em] md:text-6xl lg:text-7xl">
            SEE THE TRAIL
            <br />
            <span className="text-[#c8a66a]">
            KNOW WHAT AWAITS
            </span>
          </h1>


            <p className="mt-7 max-w-xl text-base leading-7 text-white/65 md:text-[17px]">
              Search a mountain, a trail or a whole region. Every route we
              draw is checked against OpenStreetMap first, so the shape on
              the map is the shape that is actually recorded.
            </p>


            {/* ==========================================================
                SEARCH
            ========================================================== */}

            <form
              onSubmit={
                handleSubmit
              }
              className="mt-10 w-full max-w-2xl"
            >

              <div className="flex items-center rounded-2xl border border-white/15 bg-white/[0.08] p-2 shadow-[0_25px_80px_rgba(0,0,0,0.30)] backdrop-blur-xl">

                <span className="px-4 text-xl text-[#c8a66a]">
                  ⌕
                </span>


                <input
                  type="text"
                  value={
                    query
                  }
                  onChange={(
                    event
                  ) =>
                    setQuery(
                      event.target.value
                    )
                  }
                  placeholder="Search a mountain, trail or location..."
                  autoComplete="off"
                  className="h-14 min-w-0 flex-1 bg-transparent px-2 text-base text-white outline-none placeholder:text-white/35"
                />


                <button
                  type="submit"
                  className="rounded-xl bg-[#c8a66a] px-7 py-4 text-sm font-semibold text-[#07111f] transition hover:bg-[#d4b77d]"
                >
                  Explore →
                </button>

              </div>

            </form>


            {/* ==========================================================
                FEATURE PILLS
            ========================================================== */}

            <div className="mt-9 flex flex-wrap justify-center gap-3 text-xs text-white/60 md:text-sm">

              <span className="rounded-full border border-white/10 bg-black/20 px-5 py-2.5 backdrop-blur-md">
                ⛰ Trails
              </span>


              <span className="rounded-full border border-white/10 bg-black/20 px-5 py-2.5 backdrop-blur-md">
                ◌ Conditions
              </span>


              <span className="rounded-full border border-white/10 bg-black/20 px-5 py-2.5 backdrop-blur-md">
                ◇ Gear
              </span>

            </div>


            {/* ==========================================================
                INFORMATION CARDS
            ========================================================== */}

            <div
              id="what-you-get"
              className="mt-14 grid w-full max-w-5xl scroll-mt-8 gap-4 md:grid-cols-3"
            >

              <div className="rounded-2xl border border-white/10 bg-black/20 p-6 text-left backdrop-blur-xl">

                <p className="text-xs uppercase tracking-[0.18em] text-[#c8a66a]">
                  Trails
                </p>


                <h2 className="mt-3 text-lg font-semibold">
                  Discover routes
                </h2>


                <p className="mt-2 text-sm leading-6 text-white/45">
                  Find relevant mapped trails around the
                  places you want to explore.
                </p>

              </div>


              <div className="rounded-2xl border border-white/10 bg-black/20 p-6 text-left backdrop-blur-xl">

                <p className="text-xs uppercase tracking-[0.18em] text-[#c8a66a]">
                  Conditions
                </p>


                <h2 className="mt-3 text-lg font-semibold">
                  Understand the terrain
                </h2>


                <p className="mt-2 text-sm leading-6 text-white/45">
                  Combine terrain, elevation and weather
                  information around the selected route.
                </p>

              </div>


              <div className="rounded-2xl border border-white/10 bg-black/20 p-6 text-left backdrop-blur-xl">

                <p className="text-xs uppercase tracking-[0.18em] text-[#c8a66a]">
                  Preparation
                </p>


                <h2 className="mt-3 text-lg font-semibold">
                  Go prepared
                </h2>


                <p className="mt-2 text-sm leading-6 text-white/45">
                  Turn trail and condition information into
                  practical preparation guidance.
                </p>

              </div>

            </div>

          </section>


          {/* ============================================================
              FOOTER
          ============================================================ */}

          {/*
            Attribution is a licensing requirement of OpenStreetMap, not a
            courtesy, and it is placed on every page rather than buried in a
            README nobody opens. The imagery and terrain credits are for the
            sources actually used, and no ownership is claimed over any of
            them.
          */}
          <footer className="pb-7 text-center text-xs text-white/30">
            <a
              href="https://www.openstreetmap.org/copyright"
              target="_blank"
              rel="noreferrer"
              className="underline decoration-white/20 underline-offset-2 hover:text-white/50"
            >
              Trail geometry and attributes &copy; OpenStreetMap
              contributors
            </a>
            {" · "}
            <a
              href="https://open-meteo.com/"
              target="_blank"
              rel="noreferrer"
              className="underline decoration-white/20 underline-offset-2 hover:text-white/50"
            >
              Weather and elevation from Open-Meteo
            </a>
            {" · Nothing is estimated without saying so"}
          </footer>

        </div>

      </section>

    </main>
  );
}