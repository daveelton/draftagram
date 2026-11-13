#pragma once

/** One trapezoidal integrator, the building block of a TPT filter. */
class TptIntegrator
{
public:
    /** Integrates one sample. `g` is the filter's prewarped gain, tan(pi * cutoff / sampleRate). */
    double process(double g, double input);

    /** What the integrator holds from the samples so far. */
    double state() const { return m_state; }

    void reset();

private:
    double m_state = 0.0;
};
