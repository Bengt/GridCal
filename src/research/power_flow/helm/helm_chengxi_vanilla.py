"""
Method implemented from the article:
Online voltage stability assessment for load areas based on the holomorphic embedding method
by Chengxi Liu, Bin Wang, Fengkai Hu, Kai Sun and Claus Leth Bak
Implemented by Santiago Peñate Vera 2018
"""
import numpy as np
import pandas as pd

from research.power_flow.helm.helm_chengxi_corrected import calc_W

np.set_printoptions(linewidth=32000, suppress=False)
from numpy import zeros, mod, angle, conj, array, c_, r_, linalg, Inf, complex128
from numpy.linalg import solve
from scipy.sparse.linalg import factorized
from scipy.sparse import lil_matrix
from scipy.sparse import hstack as hstack_s, vstack as vstack_s

# Set the complex precision to use
complex_type = complex128


def prepare_system_matrices(Ybus, Vbus, bus_idx, pqpv, pq, pv, ref):
    """
    Prepare the system matrices

    :param Ybus: Admittanche matrix
    :param Vbus: Node complex voltage vector (initial set voltages)
    :param pqpv: list of pq and pv bus indices
    :param ref: list of slack node indices
    :return: System matrix, initial voltage seed, initial inverse voltage seed
    """
    n_bus = len(Vbus)
    n_bus2 = 2 * n_bus
    npv = len(pv)
    # ##################################################################################################################
    # Compute the starting voltages
    # ##################################################################################################################

    # System matrix
    A = lil_matrix((n_bus2, n_bus2))  # lil matrices are faster to populate

    # Expanded slack voltages
    Vslack = zeros(n_bus2)

    # Populate A
    for a in pqpv:  # rows
        for ii in range(Ybus.indptr[a], Ybus.indptr[a + 1]):  # columns in sparse format
            b = Ybus.indices[ii]

            A[2 * a + 0, 2 * b + 0] = Ybus[a, b].real
            A[2 * a + 0, 2 * b + 1] = -Ybus[a, b].imag
            A[2 * a + 1, 2 * b + 0] = Ybus[a, b].imag
            A[2 * a + 1, 2 * b + 1] = Ybus[a, b].real

    # set vd elements
    for a in ref:
        A[a * 2, a * 2] = 1.0
        A[a * 2 + 1, a * 2 + 1] = 1.0

        Vslack[a * 2] = Vbus[a].real
        Vslack[a * 2 + 1] = Vbus[a].imag

    # Solve starting point voltages
    Vst_expanded = factorized(A.tocsc())(Vslack)

    # Invert the voltages obtained: Get the complex voltage and voltage inverse vectors
    Vst = Vst_expanded[2 * bus_idx] + 1j * Vst_expanded[2 * bus_idx + 1]
    Wst = 1.0 / Vst

    # ##################################################################################################################
    # Compute the final system matrix
    # ##################################################################################################################

    # System matrices
    B = lil_matrix((n_bus2, 3 * npv))
    C = lil_matrix((3 * npv, n_bus2))
    D = lil_matrix((3 * npv, 3 * npv))

    for i, a in enumerate(pv):
        # "a" is the actual bus index
        # "i" is the number of the pv bus in the pv buses list

        B[2 * a + 0, 3 * i + 2] = Wst[a].imag
        B[2 * a + 1, 3 * i + 2] = Wst[a].real

        C[3 * i + 0, 2 * a + 0] = Wst[a].real
        C[3 * i + 0, 2 * a + 1] = -Wst[a].imag
        C[3 * i + 1, 2 * a + 0] = Wst[a].real
        C[3 * i + 1, 2 * a + 1] = Wst[a].imag
        C[3 * i + 2, 2 * a + 0] = Vst[a].real
        C[3 * i + 2, 2 * a + 1] = Vst[a].imag

        D[3 * i + 0, 3 * i + 0] = Vst[a].real
        D[3 * i + 0, 3 * i + 1] = -Vst[a].imag
        D[3 * i + 1, 3 * i + 0] = Vst[a].imag
        D[3 * i + 1, 3 * i + 1] = Vst[a].real

    Asys = vstack_s([
                    hstack_s([A, B]),
                    hstack_s([C, D])
                    ], format="csc")

    return Asys, Vst, Wst


def get_rhs(n, V, W, Q, Vbus, Vst, Sbus, Pbus, nsys, nbus2, pv, pq, pvpos):
    """
    Right hand side
    :param n: order of the coefficients
    :param V: Voltage coefficients (order, all buses)
    :param W: Inverse voltage coefficients (order, pv buses)
    :param Q: Reactive power coefficients  (order, pv buses)
    :param Vbus: Initial bus estimate (only used to pick the PV buses set voltage)
    :param Vst: Start voltage due to slack injections
    :param Pbus: Active power injections (all the buses)
    :param nsys: number of rows or cols in the system matrix A
    :param nbus2: two times the number of buses
    :param pv: list of pv indices in the grid
    :param pvpos: array from 0..npv
    :return: right hand side vector to solve the coefficients of order n
    """
    rhs = zeros(nsys)
    m = array(range(1, n), dtype=int)
    # ##################################################################################################################
    # PQ nodes
    # ##################################################################################################################

    f1 = conj(Sbus[pq] * W[:, pq][n - 1, :])
    idx1 = 2 * pq
    rhs[idx1 + 0] = f1.real
    rhs[idx1 + 1] = f1.imag

    # ##################################################################################################################
    # PV nodes
    # ##################################################################################################################
    # Compute convolutions
    QW_convolution = (Q[n - m, :] * W[m, :][:, pv].conjugate()).sum(axis=0)  # only pv nodes
    WV_convolution = (W[n - m, :] * V[m, :]).sum(axis=0)  # all nodes
    VV_convolution = (V[m, :][:, pv] * V[n - m, :][:, pv].conjugate()).sum(axis=0)  # only pv nodes

    # compute the formulas
    f2 = Pbus[pv] * W[:, pv][n-1, :] + QW_convolution

    epsilon = -0.5 * VV_convolution
    if n == 1:
        epsilon += 0.5 * (abs(Vbus[pv]) ** 2 - abs(Vst[pv]) ** 2)

    # Assign the values to the right hand side vector
    idx2 = 2 * pv
    idx3 = 3 * pvpos + nbus2

    rhs[idx2 + 0] = f2.real
    rhs[idx2 + 1] = f2.imag

    if len(idx3) > 0:

        rhs[idx3 + 0] = -WV_convolution.real[pv]
        rhs[idx3 + 1] = -WV_convolution.imag[pv]
        rhs[idx3 + 2] = epsilon.real

    else:

        # No PV nodes
        pass

    return rhs


def assign_solution(x, bus_idx, pvpos, pv, nbus):
    """
    Assign the solution vector to the appropriate coefficients
    :param x: solution vector
    :param bus_idx: array from 0..nbus-1
    :param nbus2: two times the number of buses (integer)
    :param pvpos: array from 0..npv
    :return: Array of:
            - voltage coefficients
            - voltage inverse coefficients
            - reactive power
            of order n
    """

    nbus2 = 2 * nbus

    # declare a row of inverse voltage coefficients
    w = np.zeros(nbus, dtype=complex_type)

    # assign the voltage coefficients
    v = x[2 * bus_idx] + 1j * x[2 * bus_idx + 1]

    if len(pvpos) > 0:

        # assign the inverse voltage coefficients of the PV nodes
        w[pv] = x[nbus2 + 3 * pvpos] + 1j * x[nbus2 + 3 * pvpos + 1]

        # assign the reactive power coefficients of the PV nodes
        q = x[nbus2 + 3 * pvpos + 2]

    else:

        # No PV nodes

        w = zeros(0)

        q = zeros(0)

    return v, w, q


def pade_approximation(n, an, s=1):
    """
    Computes the n/2 pade approximant of the series an at the approximation
    point s
    Arguments:
        an: coefficient matrix, (number of coefficients, number of series)
        n:  order of the series
        s: point of approximation
    Returns:
        pade approximation at s
    """
    nn = int(n / 2)
    if mod(nn, 2) == 0:
        nn -= 1

    L = nn
    M = nn

    an = np.ndarray.flatten(an)
    rhs = an[L + 1:L + M + 1]

    C = zeros((L, M), dtype=complex_type)
    for i in range(L):
        k = i + 1
        C[i, :] = an[L - M + k:L + k]

    try:
        b = solve(C, -rhs)  # bn to b1
    except:
        return 0, zeros(L + 1, dtype=complex_type), zeros(L + 1, dtype=complex_type)

    b = r_[1, b[::-1]]  # b0 = 1

    a = zeros(L + 1, dtype=complex_type)
    a[0] = an[0]
    for i in range(L):
        val = complex_type(0)
        k = i + 1
        for j in range(k + 1):
            val += an[k - j] * b[j]
        a[i + 1] = val

    p = complex_type(0)
    q = complex_type(0)
    for i in range(L + 1):
        p += a[i] * s ** i
        q += b[i] * s ** i

    return p / q, a, b


def helm_chengxi_vanilla(
        *,
        bus_voltages, complex_bus_powers, bus_admittances,
        pq_bus_indices, pv_bus_indices, slack_bus_indices,
        pq_and_pv_bus_indices
):
    """
    Helm Method

    :param bus_voltages: List of bus voltages
    :param complex_bus_powers: List of complex power injections/extractions
    :param bus_admittances: Matrix of bus admittances
    :param pq_bus_indices: List of pq bus indices
    :param pv_bus_indices: List of pv bus indices
    :param slack_bus_indices: List of slack bus indices
    :param pq_and_pv_bus_indices: List of pq and pv node indices sorted

    :return: Voltage array and the power mismatch
    """
    converged = None  # TODO Get this from algorithm
    it = None  # TODO Get this from algorithm
    el = None  # TODO Get this from algorithm
    normF = None  # TODO Get this from algorithm

    nbus = len(bus_voltages)
    npv = len(pv_bus_indices)
    bus_idx = array(range(nbus), dtype=int)
    pvpos = array(range(npv), dtype=int)

    # Prepare system matrices
    Asys, Vst, Wst = prepare_system_matrices(
        bus_admittances, bus_voltages, bus_idx, pq_and_pv_bus_indices,
        pq_bus_indices, pv_bus_indices, slack_bus_indices
    )

    # Factorize the system matrix
    Afact = factorized(Asys)

    # get the shape
    nsys = Asys.shape[0]

    # declare the active power injections
    Pbus = complex_bus_powers.real

    # declare the matrix of coefficients: [order, bus index]
    V = zeros((1, nbus), dtype=complex_type)

    # Declare the inverse voltage coefficients: [order, pv bus index]
    W = zeros((1, nbus), dtype=complex_type)

    # Reactive power coefficients on the PV nodes: [order, pv bus index]
    Q = zeros((1, npv), dtype=np.double)

    # Assign the initial values
    V[0, :] = Vst
    W[0, :] = Wst
    Q[0, :] = zeros(npv)

    error = list()

    for n in range(1, 15):

        # Compute the free terms
        rhs = get_rhs(n=n, V=V, W=W, Q=Q,
                      Vbus=bus_voltages, Vst=Vst,
                      Sbus=complex_bus_powers,
                      Pbus=Pbus, nsys=nsys,
                      nbus2=2 * nbus,
                      pv=pv_bus_indices, pq=pq_bus_indices,
                      pvpos=pvpos)

        # Solve the linear system Asys x res = rhs
        res = Afact(rhs)

        # get the new rows of coefficients
        v, w, q = assign_solution(x=res, bus_idx=bus_idx, pvpos=pvpos, pv=pv_bus_indices, nbus=nbus)

        # Add coefficients row
        V = np.vstack((V, v))

        w = calc_W(n, V, W)
        W = np.vstack((W, w))
        Q = np.vstack((Q, q))

        #     print('\nn:', n)
        #     print('RHS:\n', rhs)
        #     print('X:\n', res)
        #
        # print('V:\n', V)
        # print('W:\n', W)
        # print('Q:\n', Q)

        # Perform the Padè approximation
        # NOTE: Apparently the padé approximation is equivalent to the bare sum of coefficients !!
        # voltage = zeros(nbus, dtype=complex_type)
        # for i in range(nbus):
        #     voltage[i], _, _ = pade_approximation(n, V[:, i])

        voltage = V.sum(axis=0)

        # Calculate the error and check the convergence
        Scalc = voltage * conj(bus_admittances * voltage)
        mismatch = Scalc - complex_bus_powers  # complex power mismatch
        power_mismatch_ = r_[mismatch[pv_bus_indices].real, mismatch[pq_bus_indices].real, mismatch[pq_bus_indices].imag]

        # check for convergence
        normF = linalg.norm(power_mismatch_, Inf)

        if npv > 0:
            a = linalg.norm(mismatch[pv_bus_indices].real, Inf)
        else:
            a = 0
        b = linalg.norm(mismatch[pq_bus_indices].real, Inf)
        c = linalg.norm(mismatch[pq_bus_indices].imag, Inf)
        error.append([a, b, c])

    err_df = pd.DataFrame(array(error), columns=['PV_real', 'PQ_real', 'PQ_imag'])
    err_df.plot(logy=True)

    return voltage, converged, normF, Scalc, it, el